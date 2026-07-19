from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.settings import settings


def test_health_endpoints():
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200


def test_upload_history_and_safe_render(tmp_path: Path):
    settings.local_storage = True
    settings.local_storage_root = tmp_path
    with TestClient(app) as client:
        response = client.post("/upload", files={"file": ("soh.csv", b'"1","<script>","2.0"\n', "text/csv")}, follow_redirects=True)
        assert response.status_code == 200
        assert "&lt;script&gt;" in response.text
        history = client.get("/history")
        assert "soh.csv" in history.text


def test_rejects_non_csv():
    with TestClient(app) as client:
        assert client.post("/upload", files={"file": ("x.txt", b"x", "text/plain")}).status_code == 400
