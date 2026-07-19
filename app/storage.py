import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import boto3

from .settings import settings


@dataclass(frozen=True)
class StoredObject:
    key: str
    original_name: str
    row_count: int
    processed_at: str
    sha256: str


class ObjectStorage:
    """S3 is both the durable object store and the stateless history index."""

    def _client(self):
        return boto3.client("s3", region_name=settings.aws_region)

    def put(self, source: Path, key: str, metadata: dict[str, str]) -> str:
        if settings.local_storage:
            target = settings.local_storage_root / key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.with_suffix(target.suffix + ".metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            return f"file://{target.resolve()}"
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required when LOCAL_STORAGE=false")
        self._client().upload_file(
            str(source), settings.s3_bucket, key,
            ExtraArgs={"ContentType": "text/csv", "Metadata": metadata},
        )
        return f"s3://{settings.s3_bucket}/{key}"

    def read(self, key: str) -> bytes:
        if settings.local_storage:
            return (settings.local_storage_root / key).read_bytes()
        return self._client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()

    def list(self, limit: int = 100) -> list[StoredObject]:
        if settings.local_storage:
            sidecars = sorted(
                settings.local_storage_root.glob(f"{settings.s3_prefix}*.csv.metadata.json"),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )[:limit]
            result = []
            for sidecar in sidecars:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                key = sidecar.relative_to(settings.local_storage_root).as_posix().removesuffix(".metadata.json")
                result.append(StoredObject(key=key, **data))
            return result
        response = self._client().list_objects_v2(
            Bucket=settings.s3_bucket, Prefix=settings.s3_prefix, MaxKeys=limit
        )
        result = []
        for item in response.get("Contents", []):
            head = self._client().head_object(Bucket=settings.s3_bucket, Key=item["Key"])
            meta = head.get("Metadata", {})
            result.append(StoredObject(key=item["Key"], original_name=meta.get("original_name", item["Key"]), row_count=int(meta.get("row_count", 0)), processed_at=meta.get("processed_at", item["LastModified"].isoformat()), sha256=meta.get("sha256", "")))
        return result


storage = ObjectStorage()
