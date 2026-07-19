from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    local_storage: bool = True
    local_storage_root: Path = Path("data/objects")
    s3_bucket: str = ""
    aws_region: str = "us-east-1"
    s3_prefix: str = "processed/"
    dynamodb_table: str = "csv-processing-jobs"
    max_upload_bytes: int = 10 * 1024 * 1024
    preview_rows: int = 200


settings = Settings()
