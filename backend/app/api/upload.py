import os
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Depends
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


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    level: str = Form("standard"),
    custom_entities: Optional[str] = Form(None),   # JSON array as string
    profile_name: Optional[str] = Form(None),
    output_mode: str = Form("directory"),
    webhook_url: Optional[str] = Form(None),
    webhook_headers: Optional[str] = Form(None),      # JSON object: {"Header": "value", ...}
    webhook_secret: Optional[str] = Form(None),       # HMAC-SHA256 signing secret
    webhook_include_file: bool = Form(False),          # embed base64 file in payload
    webhook_template: Optional[str] = Form(None),      # named Jinja2 payload template
    webhook_extra: Optional[str] = Form(None),         # JSON object — extra vars merged into template context
    db: AsyncSession = Depends(get_db),
):
    # Validate level
    valid_levels = {e.value for e in RedactionLevel}
    if level not in valid_levels:
        raise HTTPException(status_code=400, detail=f"Invalid level. Choose from: {', '.join(valid_levels)}")

    # Parse webhook extra template vars
    parsed_webhook_extra = None
    if webhook_extra:
        import json as _json
        try:
            parsed_webhook_extra = _json.loads(webhook_extra)
            if not isinstance(parsed_webhook_extra, dict):
                raise ValueError
        except Exception:
            raise HTTPException(status_code=400, detail="webhook_extra must be a valid JSON object")

    # Parse webhook headers
    parsed_webhook_headers = None
    if webhook_headers:
        import json as _json
        try:
            parsed_webhook_headers = _json.loads(webhook_headers)
            if not isinstance(parsed_webhook_headers, dict):
                raise ValueError
        except Exception:
            raise HTTPException(status_code=400, detail="webhook_headers must be a valid JSON object")

    # Parse custom entities
    parsed_entities = None
    if level == "custom" or custom_entities:
        import json as _json
        try:
            parsed_entities = _json.loads(custom_entities) if custom_entities else []
        except Exception:
            raise HTTPException(status_code=400, detail="custom_entities must be a valid JSON array")

    # If a profile name is provided, load its entities
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
        webhook_include_file=webhook_include_file if output_mode == "webhook" else False,
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
