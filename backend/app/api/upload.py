import json as _json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, get_runtime_value
from app.database import get_db
from app.models.job import Job, JobStatus, RedactionLevel, OutputMode
from app.utils.file_utils import compute_sha256, get_temp_path

router = APIRouter()

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}


def _validate_file(filename: str, size: int) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")
    max_bytes = get_runtime_value("max_file_size_mb", settings.max_file_size_mb) * 1024 * 1024
    if size > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large (max {max_bytes // (1024*1024)}MB)")


def _parse_json_field(value: Optional[str], field_name: str, expect_type: type):
    """Parse a JSON string form field, raising HTTP 400 on failure."""
    if not value:
        return None
    try:
        parsed = _json.loads(value)
        if not isinstance(parsed, expect_type):
            raise ValueError
        return parsed
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a valid JSON {expect_type.__name__}")


def _extract_prefixed(d: dict, prefix: str) -> dict:
    """Extract keys starting with prefix from a dict, stripping the prefix."""
    return {k[len(prefix):]: str(v) for k, v in d.items() if k.startswith(prefix) and k[len(prefix):]}


async def _collect_prefixed_headers(request: Request) -> dict:
    """Collect webhook_header_<Name> form fields into a headers dict."""
    form = await request.form()
    result = {}
    for key, value in form.multi_items():
        if key.startswith("webhook_header_"):
            name = key[len("webhook_header_"):]
            if name:
                result[name] = str(value)
    return result


async def _collect_prefixed_extra(request: Request) -> dict:
    """Collect webhook_extra_<key> form fields into an extra-vars dict.
    Values are kept as strings; templates render them verbatim so integer
    fields like {{ doc_no }} in a JSON body still produce unquoted numbers.
    """
    form = await request.form()
    result = {}
    for key, value in form.multi_items():
        if key.startswith("webhook_extra_"):
            name = key[len("webhook_extra_"):]
            if name:
                result[name] = str(value)
    return result


@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    # Therefore-compatible metadata envelope: all params can be sent as a single
    # JSON object in the "metadata" multipart part (Therefore's Body tab) instead
    # of individual form fields. Individual fields take precedence if both supplied.
    metadata: Optional[str] = Form(None),
    level: Optional[str] = Form(None),
    custom_entities: Optional[str] = Form(None),   # JSON array as string
    profile_name: Optional[str] = Form(None),
    output_mode: Optional[str] = Form(None),
    webhook_url: Optional[str] = Form(None),
    webhook_headers: Optional[str] = Form(None),      # JSON object: {"Header": "value", ...}
    webhook_secret: Optional[str] = Form(None),       # HMAC-SHA256 signing secret
    webhook_include_file: Optional[str] = Form(None), # "true"/"false" string to support JSON metadata
    webhook_template: Optional[str] = Form(None),     # named Jinja2 payload template
    webhook_extra: Optional[str] = Form(None),        # JSON object — extra vars merged into template context
    db: AsyncSession = Depends(get_db),
):
    # ── Merge metadata envelope (Therefore Body tab sends everything as one JSON part)
    meta = {}
    if metadata:
        meta = _parse_json_field(metadata, "metadata", dict) or {}

    def _get(field_value, key, default=None):
        """Return explicit form field if provided, else fall back to metadata envelope."""
        if field_value is not None:
            return field_value
        return meta.get(key, default)

    level           = _get(level,           "level",           "standard")
    custom_entities = _get(custom_entities, "custom_entities")
    profile_name    = _get(profile_name,    "profile_name")
    output_mode     = _get(output_mode,     "output_mode",     "directory")
    webhook_url     = _get(webhook_url,     "webhook_url")
    webhook_secret  = _get(webhook_secret,  "webhook_secret")
    webhook_template= _get(webhook_template,"webhook_template")

    # webhook_include_file arrives as a string in both paths; normalise to bool
    _wif_raw        = _get(webhook_include_file, "webhook_include_file", "false")
    webhook_include_file_bool = str(_wif_raw).lower() in ("true", "1", "yes")

    # webhook_headers — may already be a dict (from metadata envelope) or a JSON string
    _wh_raw         = _get(webhook_headers, "webhook_headers")
    if isinstance(_wh_raw, dict):
        parsed_webhook_headers = _wh_raw
    else:
        parsed_webhook_headers = _parse_json_field(_wh_raw, "webhook_headers", dict)

    # webhook_extra — same treatment
    _we_raw         = _get(webhook_extra, "webhook_extra")
    if isinstance(_we_raw, dict):
        parsed_webhook_extra = _we_raw
    else:
        parsed_webhook_extra = _parse_json_field(_we_raw, "webhook_extra", dict)

    # Merge prefixed keys from the metadata envelope, then from form fields (form wins)
    meta_headers = _extract_prefixed(meta, "webhook_header_")
    meta_extra   = _extract_prefixed(meta, "webhook_extra_")
    prefix_headers = {**meta_headers, **(await _collect_prefixed_headers(request))}
    prefix_extra   = {**meta_extra,   **(await _collect_prefixed_extra(request))}
    if prefix_headers:
        parsed_webhook_headers = {**(parsed_webhook_headers or {}), **prefix_headers}
    if prefix_extra:
        parsed_webhook_extra = {**(parsed_webhook_extra or {}), **prefix_extra}

    # ── Validate level
    valid_levels = {e.value for e in RedactionLevel}
    if level not in valid_levels:
        raise HTTPException(status_code=400, detail=f"Invalid level. Choose from: {', '.join(valid_levels)}")

    # ── Parse custom entities
    parsed_entities = None
    if level == "custom" or custom_entities:
        if isinstance(custom_entities, list):
            parsed_entities = custom_entities
        else:
            parsed_entities = _parse_json_field(custom_entities, "custom_entities", list) or []

    # ── If a profile name is provided, load its entities
    if profile_name:
        from app.config import load_runtime_config
        config = load_runtime_config()
        profiles = config.get("profiles", {})
        if profile_name not in profiles:
            raise HTTPException(status_code=400, detail=f"Profile '{profile_name}' not found")
        parsed_entities = profiles[profile_name].get("entities", [])
        level = "custom"

    # Read file content
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    _validate_file(file.filename, len(content))

    # Save to temp location
    job_id = str(uuid.uuid4())
    temp_path = get_temp_path(file.filename)
    os.makedirs(Path(temp_path).parent, exist_ok=True)
    with open(temp_path, "wb") as f:
        f.write(content)

    file_hash = compute_sha256(temp_path)

    job = Job(
        id=job_id,
        filename=file.filename,
        file_hash=file_hash,
        file_size_bytes=len(content),
        mime_type=file.content_type,
        source="api",
        status=JobStatus.QUEUED,
        level=level,
        custom_entities=parsed_entities,
        profile_name=profile_name,
        output_mode=output_mode,
        webhook_url=webhook_url if output_mode == "webhook" else None,
        webhook_headers=parsed_webhook_headers if output_mode == "webhook" else None,
        webhook_secret=webhook_secret if output_mode == "webhook" else None,
        webhook_include_file=webhook_include_file_bool if output_mode == "webhook" else False,
        webhook_template=webhook_template if output_mode == "webhook" else None,
        webhook_extra=parsed_webhook_extra if output_mode == "webhook" else None,
        input_path=temp_path,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    return {
        "status": "queued",
        "job_id": job_id,
        "filename": file.filename,
        "level": level,
    }


@router.post("/upload-sync")
async def upload_document_sync(
    request: Request,
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(None),
    level: Optional[str] = Form(None),
    custom_entities: Optional[str] = Form(None),
    profile_name: Optional[str] = Form(None),
    output_mode: Optional[str] = Form(None),
    webhook_url: Optional[str] = Form(None),
    webhook_headers: Optional[str] = Form(None),
    webhook_secret: Optional[str] = Form(None),
    webhook_include_file: Optional[str] = Form(None),
    webhook_template: Optional[str] = Form(None),
    webhook_extra: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a document for redaction and block until processing and delivery are complete.
    Accepts the same parameters as /upload. Returns the completed job result.
    Use this when the caller (e.g. a Therefore workflow REST Call task) needs to wait
    for the result before continuing.
    """
    from app.workers.job_processor import process_job_now

    # ── Reuse the same parameter parsing as /upload ──────────────────────────
    meta = {}
    if metadata:
        meta = _parse_json_field(metadata, "metadata", dict) or {}

    def _get(field_value, key, default=None):
        if field_value is not None:
            return field_value
        return meta.get(key, default)

    level            = _get(level,            "level",            "standard")
    custom_entities  = _get(custom_entities,  "custom_entities")
    profile_name     = _get(profile_name,     "profile_name")
    output_mode      = _get(output_mode,      "output_mode",      "directory")
    webhook_url      = _get(webhook_url,      "webhook_url")
    webhook_secret   = _get(webhook_secret,   "webhook_secret")
    webhook_template = _get(webhook_template, "webhook_template")

    _wif_raw = _get(webhook_include_file, "webhook_include_file", "false")
    webhook_include_file_bool = str(_wif_raw).lower() in ("true", "1", "yes")

    _wh_raw = _get(webhook_headers, "webhook_headers")
    if isinstance(_wh_raw, dict):
        parsed_webhook_headers = _wh_raw
    else:
        parsed_webhook_headers = _parse_json_field(_wh_raw, "webhook_headers", dict)

    _we_raw = _get(webhook_extra, "webhook_extra")
    if isinstance(_we_raw, dict):
        parsed_webhook_extra = _we_raw
    else:
        parsed_webhook_extra = _parse_json_field(_we_raw, "webhook_extra", dict)

    # Merge prefixed keys from the metadata envelope, then from form fields (form wins)
    meta_headers = _extract_prefixed(meta, "webhook_header_")
    meta_extra   = _extract_prefixed(meta, "webhook_extra_")
    prefix_headers = {**meta_headers, **(await _collect_prefixed_headers(request))}
    prefix_extra   = {**meta_extra,   **(await _collect_prefixed_extra(request))}
    if prefix_headers:
        parsed_webhook_headers = {**(parsed_webhook_headers or {}), **prefix_headers}
    if prefix_extra:
        parsed_webhook_extra = {**(parsed_webhook_extra or {}), **prefix_extra}

    valid_levels = {e.value for e in RedactionLevel}
    if level not in valid_levels:
        raise HTTPException(status_code=400, detail=f"Invalid level. Choose from: {', '.join(valid_levels)}")

    parsed_entities = None
    if level == "custom" or custom_entities:
        if isinstance(custom_entities, list):
            parsed_entities = custom_entities
        else:
            parsed_entities = _parse_json_field(custom_entities, "custom_entities", list) or []

    if profile_name:
        from app.config import load_runtime_config
        config = load_runtime_config()
        profiles = config.get("profiles", {})
        if profile_name not in profiles:
            raise HTTPException(status_code=400, detail=f"Profile '{profile_name}' not found")
        parsed_entities = profiles[profile_name].get("entities", [])
        level = "custom"

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    _validate_file(file.filename, len(content))

    job_id = str(uuid.uuid4())
    temp_path = get_temp_path(file.filename)
    os.makedirs(Path(temp_path).parent, exist_ok=True)
    with open(temp_path, "wb") as f:
        f.write(content)

    file_hash = compute_sha256(temp_path)
    now = datetime.now(timezone.utc)

    # Create the job already in PROCESSING state so the async worker ignores it
    job = Job(
        id=job_id,
        filename=file.filename,
        file_hash=file_hash,
        file_size_bytes=len(content),
        mime_type=file.content_type,
        source="api",
        status=JobStatus.PROCESSING,
        started_at=now,
        level=level,
        custom_entities=parsed_entities,
        profile_name=profile_name,
        output_mode=output_mode,
        webhook_url=webhook_url if output_mode == "webhook" else None,
        webhook_headers=parsed_webhook_headers if output_mode == "webhook" else None,
        webhook_secret=webhook_secret if output_mode == "webhook" else None,
        webhook_include_file=webhook_include_file_bool if output_mode == "webhook" else False,
        webhook_template=webhook_template if output_mode == "webhook" else None,
        webhook_extra=parsed_webhook_extra if output_mode == "webhook" else None,
        input_path=temp_path,
    )
    db.add(job)
    await db.commit()

    # Block until processing and delivery are complete
    await process_job_now(job_id)

    # Read final state and return
    await db.refresh(job)
    if job.status == JobStatus.FAILED:
        raise HTTPException(status_code=500, detail=job.error_message or "Redaction failed")

    return {
        "status": "completed",
        "job_id": job_id,
        "filename": job.filename,
        "level": level,
        "page_count": job.page_count,
        "entities_found": job.entities_found,
        "processing_ms": job.processing_ms,
        "webhook_sent": job.webhook_sent,
    }
