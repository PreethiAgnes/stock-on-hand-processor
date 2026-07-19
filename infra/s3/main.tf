terraform {
  required_version = ">= 1.6.0"
  backend "s3" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "bucket_name" { type = string }
variable "allow_bucket_destroy" {
  description = "Permit deletion of a non-empty versioned data bucket. Enable only during an approved destroy operation."
  type        = bool
  default     = false
}
variable "region" {
  type    = string
  default = "us-east-1"
}
variable "oidc_provider_arn" {
  description = "kOps cluster IAM OIDC provider ARN; leave empty to skip IRSA role creation"
  type        = string
  default     = ""
}
variable "oidc_provider_url" {
  description = "OIDC issuer host/path without https://"
  type        = string
  default     = ""
}
variable "service_account_subject" {
  type    = string
  default = "system:serviceaccount:csv-processor:csv-processor-csv-processor"
}

provider "aws" { region = var.region }

resource "aws_s3_bucket" "uploads" {
  bucket        = var.bucket_name
  force_destroy = var.allow_bucket_destroy
}

resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  depends_on = [aws_s3_bucket_versioning.uploads]
  bucket     = aws_s3_bucket.uploads.id
  rule {
    id     = "archive-processed-csv"
    status = "Enabled"
    filter { prefix = "processed/" }
    transition {
      days          = 30
      storage_class = "GLACIER"
    }
    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER"
    }
  }
}

resource "aws_dynamodb_table" "jobs" {
  name         = "csv-processing-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"
  attribute {
    name = "job_id"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
}

data "aws_iam_policy_document" "workload" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/processed/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.uploads.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["processed/*"]
    }
  }
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan"]
    resources = [aws_dynamodb_table.jobs.arn]
  }
}

resource "aws_iam_policy" "workload" {
  name   = "csv-processor-workload"
  policy = data.aws_iam_policy_document.workload.json
}

data "aws_iam_policy_document" "irsa_trust" {
  count = var.oidc_provider_arn == "" ? 0 : 1
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = [var.service_account_subject]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "workload" {
  count              = var.oidc_provider_arn == "" ? 0 : 1
  name               = "csv-processor-irsa"
  assume_role_policy = data.aws_iam_policy_document.irsa_trust[0].json
}

resource "aws_iam_role_policy_attachment" "workload" {
  count      = var.oidc_provider_arn == "" ? 0 : 1
  role       = aws_iam_role.workload[0].name
  policy_arn = aws_iam_policy.workload.arn
}

output "bucket_name" { value = aws_s3_bucket.uploads.id }
output "dynamodb_table" { value = aws_dynamodb_table.jobs.name }
output "workload_policy_arn" { value = aws_iam_policy.workload.arn }
output "irsa_role_arn" { value = try(aws_iam_role.workload[0].arn, null) }
