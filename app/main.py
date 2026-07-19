import csv
import hashlib
import html
import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from .settings import settings
from .jobs import jobs
from .storage import storage

app = FastAPI(title="Stock-on-Hand CSV Processor", version="2.0.0")


def page(title: str, body: str) -> HTMLResponse:
    safe_title = html.escape(title)
    return HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
    <meta name='viewport' content='width=device-width,initial-scale=1'><title>{safe_title}</title>
    <link rel='stylesheet' href='/static/site.css'></head><body><main><header><span class='eyebrow'>CASE STUDY</span><h1>{safe_title}</h1>
    <nav><a href='/'>Process file</a><a href='/history'>History</a></nav></header>{body}</main></body></html>""")


def parse_csv(content: bytes) -> list[list[str]]:
    try:
        text = content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(400, f"Invalid UTF-8 CSV: {exc}") from exc
    if not rows:
        raise HTTPException(400, "The CSV has no data rows")
    return rows


def quality_report(rows: list[list[str]]) -> dict[str, int]:
    widths = [len(row) for row in rows]
    expected = max(set(widths), key=widths.count)
    return {
        "row_count": len(rows),
        "expected_columns": expected,
        "inconsistent_rows": sum(width != expected for width in widths),
        "blank_cells": sum(not cell.strip() for row in rows for cell in row),
    }


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return page("Stock-on-Hand Processor", """<section class='card'><h2>Upload CSV</h2>
    <p>Upload the supplied stock-on-hand format. The service validates, previews, fingerprints and archives the original.</p>
    <form action='/upload' method='post' enctype='multipart/form-data'><label for='file'>CSV file</label>
    <input id='file' required name='file' type='file' accept='.csv,text/csv'><button type='submit'>Process and archive</button></form></section>""")


@app.post("/upload")
async def upload(file: UploadFile = File(...)) -> RedirectResponse:
    original_name = Path(file.filename or "").name
    if not original_name.lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted")
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, "File exceeds the configured upload limit")
    record_id = str(uuid4())
    digest = hashlib.sha256(content).hexdigest()
    jobs.transition(record_id, "RECEIVED", original_name=original_name, checksum_sha256=digest)
    try:
        rows = parse_csv(content)
        report = quality_report(rows)
        jobs.transition(record_id, "VALIDATED", quality=report)
    except HTTPException as exc:
        jobs.transition(record_id, "FAILED", error=str(exc.detail))
        raise
    key = f"{settings.s3_prefix.rstrip('/')}/{record_id}.csv"
    processed_at = datetime.now(timezone.utc).isoformat()
    metadata = {"original_name": original_name, "row_count": str(len(rows)), "processed_at": processed_at, "sha256": digest}
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        temp_path = Path(tmp.name)
    try:
        object_uri = storage.put(temp_path, key, metadata)
    finally:
        temp_path.unlink(missing_ok=True)
    jobs.transition(record_id, "ARCHIVED", object_key=key, object_uri=object_uri, processed_at=processed_at, quality=report)
    return RedirectResponse(f"/files/{record_id}", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history() -> HTMLResponse:
    records = jobs.list()
    body = "".join(f"<tr><td><a href='/files/{html.escape(r['job_id'])}'>{html.escape(r.get('original_name','unknown'))}</a></td><td><span>{html.escape(r.get('state','UNKNOWN'))}</span></td><td>{r.get('quality',{}).get('row_count','-')}</td><td>{html.escape(r.get('updated_at',''))}</td></tr>" for r in records)
    return page("Processing history", f"<section class='card table-wrap'><table><thead><tr><th>File</th><th>State</th><th>Rows</th><th>Updated UTC</th></tr></thead><tbody>{body or '<tr><td colspan=4>No files processed yet.</td></tr>'}</tbody></table></section>")


@app.get("/files/{record_id}", response_class=HTMLResponse)
def processed_file(record_id: str) -> HTMLResponse:
    try:
        canonical_id = str(__import__('uuid').UUID(record_id))
        job = jobs.get(canonical_id)
        if not job or job.get("state") != "ARCHIVED":
            raise FileNotFoundError
        content = storage.read(job["object_key"])
    except (ValueError, FileNotFoundError, KeyError):
        raise HTTPException(404, "Processed file not found")
    rows = parse_csv(content)
    preview = rows[: settings.preview_rows]
    rendered = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in preview)
    q = quality_report(rows)
    note = f"Showing first {len(preview)} of {len(rows)} rows. SHA-256: {html.escape(job['checksum_sha256'])}. Quality: {q['inconsistent_rows']} inconsistent rows, {q['blank_cells']} blank cells."
    return page("Processed CSV", f"<section class='card'><p>{note}</p><div class='table-wrap'><table><tbody>{rendered}</tbody></table></div></section>")
