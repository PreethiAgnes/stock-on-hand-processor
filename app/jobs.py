import json
from datetime import datetime, timezone
from pathlib import Path

import boto3

from .settings import settings


class JobStore:
    """Minimal job ledger: local JSON for development, DynamoDB in AWS."""

    def _table(self):
        return boto3.resource("dynamodb", region_name=settings.aws_region).Table(settings.dynamodb_table)

    def _path(self, job_id: str) -> Path:
        return settings.local_storage_root / "jobs" / f"{job_id}.json"

    def put(self, item: dict) -> None:
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        if settings.local_storage:
            path = self._path(item["job_id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(item, indent=2), encoding="utf-8")
        else:
            self._table().put_item(Item=item)

    def transition(self, job_id: str, state: str, **fields) -> dict:
        item = self.get(job_id) or {"job_id": job_id}
        item.update(fields)
        item["state"] = state
        self.put(item)
        return item

    def get(self, job_id: str) -> dict | None:
        if settings.local_storage:
            path = self._path(job_id)
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return self._table().get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")

    def list(self, limit: int = 100) -> list[dict]:
        if settings.local_storage:
            paths = sorted((settings.local_storage_root / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            return [json.loads(p.read_text(encoding="utf-8")) for p in paths[:limit]]
        # A small case-study table uses Scan. At higher volume, add a created_at GSI and Query it.
        items = self._table().scan(Limit=limit).get("Items", [])
        return sorted(items, key=lambda i: i.get("updated_at", ""), reverse=True)


jobs = JobStore()
