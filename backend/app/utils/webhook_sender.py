import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from jinja2 import Template, TemplateError

logger = logging.getLogger(__name__)

_WEBHOOK_TIMEOUT_DEFAULT = 15.0    # seconds — normal JSON payloads
_WEBHOOK_TIMEOUT_LARGE   = 60.0    # seconds — large payloads or file attachments
_WEBHOOK_LARGE_BODY_BYTES = 100_000 # threshold above which the longer timeout is used

_BASE64_BLOB_RE = re.compile(
    r'("(?:FileData|FileDataBase64JSON|file_data)"\s*:\s*)"[A-Za-z0-9+/=]{100,}"'
)


async def send_webhook(
    url: str,
    payload: dict,
    secret: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    max_retries: int = 3,
    raw_body: Optional[bytes] = None,
) -> bool:
    """
    Send a signed webhook POST. Returns True on success.
    If raw_body is provided it is used as-is (e.g. a rendered Jinja2 template);
    otherwise payload is serialised to JSON.
    extra_headers are merged last so they can override any default header.
    Retries with exponential backoff on failure.
    """
    body = raw_body if raw_body is not None else json.dumps(payload, default=str).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Redactor-Timestamp": str(int(time.time())),
        "User-Agent": "Redactor/1.0",
    }
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Redactor-Signature"] = f"sha256={sig}"
    if extra_headers:
        headers.update(extra_headers)

    timeout = _WEBHOOK_TIMEOUT_LARGE if (raw_body and len(raw_body) > _WEBHOOK_LARGE_BODY_BYTES) or payload.get("file_data") else _WEBHOOK_TIMEOUT_DEFAULT
    # Log the full body being sent, redacting any base64 file data to keep logs readable
    try:
        log_body = body.decode("utf-8", errors="replace")
        # Replace large base64 blobs with a size marker
        log_body = _BASE64_BLOB_RE.sub(
            lambda m: m.group(0)[:m.start(1) - m.start(0) + len(m.group(1))] + f'"[base64 {len(m.group(0))} chars]"',
            log_body,
        )
        logger.info(f"Webhook POST to {url}:\n{log_body}")
    except Exception:
        logger.info(f"Webhook POST to {url}: {len(body)} bytes")
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_retries):
            try:
                resp = await client.post(url, content=body, headers=headers)
                if resp.status_code < 400:
                    logger.info(f"Webhook delivered to {url} (status {resp.status_code})")
                    return True
                logger.warning(
                    f"Webhook attempt {attempt+1} failed: HTTP {resp.status_code} — {resp.text[:500]}"
                )
            except Exception as e:
                logger.warning(f"Webhook attempt {attempt+1} error: {e}")

            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

    logger.error(f"Webhook permanently failed after {max_retries} attempts: {url}")
    return False


def build_job_payload(job, download_base_url: str = "") -> dict:
    """
    Build the webhook payload for a completed job.
    If job.webhook_include_file is True, reads the output file and embeds it
    as base64 in file_data — ready to drop straight into Therefore's
    CreateDocument.Streams[0].FileData.
    """
    payload = {
        "event": "job.completed",
        "job_id": job.id,
        "filename": job.filename,
        "status": job.status,
        "level": job.level,
        "page_count": job.page_count,
        "entities_found": job.entities_found or {},
        "processing_ms": job.processing_ms,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "download_url": f"{download_base_url}/api/v1/jobs/{job.id}/download" if download_base_url else None,
    }

    if job.webhook_include_file and job.output_path and os.path.exists(job.output_path):
        with open(job.output_path, "rb") as f:
            file_bytes = f.read()
        payload["file_data"] = base64.b64encode(file_bytes).decode("ascii")
        payload["file_size_bytes"] = len(file_bytes)
        payload["file_name"] = os.path.basename(job.output_path)

    return payload


def build_template_context(job) -> dict:
    """Build the Jinja2 template variable context from a completed job."""
    context = {
        "job_id": job.id,
        "filename": job.filename,
        "stem": Path(job.filename).stem,
        "status": str(job.status).split(".")[-1].lower(),   # "completed" not "JobStatus.COMPLETED"
        "level": str(job.level).split(".")[-1].lower(),     # "standard" not "RedactionLevel.STANDARD"
        "page_count": job.page_count or 0,
        "entities_found": job.entities_found or {},
        "total_entities": sum((job.entities_found or {}).values()),
        "processing_ms": job.processing_ms,
        "completed_at": (
            job.completed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            if job.completed_at else None
        ),
        # file data fields — populated below if file is available
        "file_data": "",
        "file_name": "",
        "file_size_bytes": 0,
    }

    if job.output_path and os.path.exists(job.output_path):
        with open(job.output_path, "rb") as f:
            file_bytes = f.read()
        context["file_data"] = base64.b64encode(file_bytes).decode("ascii")
        context["file_size_bytes"] = len(file_bytes)
        context["file_name"] = os.path.basename(job.output_path)

    # Merge caller-supplied extra vars — they can override anything above
    if job.webhook_extra:
        context.update(job.webhook_extra)

    return context


async def fetch_pre_fetch_context(
    url: str,
    headers: Optional[dict] = None,
    method: str = "GET",
    body: Optional[str] = None,
) -> dict:
    """Call a URL before rendering the main template and return the parsed JSON response.
    Result is made available as `fetched` in the template context."""
    try:
        async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT_DEFAULT) as client:
            req_headers = {"Content-Type": "application/json", **(headers or {})}
            if method.upper() == "POST":
                resp = await client.post(url, content=(body or "").encode(), headers=req_headers)
            else:
                resp = await client.get(url, headers=req_headers)
            if resp.status_code < 400:
                return resp.json()
            logger.warning(f"pre_fetch {method} {url} returned HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"pre_fetch {method} {url} failed: {e}")
    return {}


async def render_webhook_template(
    template_str: str,
    job,
    pre_fetch_url: Optional[str] = None,
    pre_fetch_headers: Optional[dict] = None,
    pre_fetch_method: str = "GET",
    pre_fetch_body: Optional[str] = None,
) -> bytes:
    """
    Render a Jinja2 webhook template with job context.
    If pre_fetch_url is set, calls that URL first and injects the response JSON
    as `fetched` into the template context (e.g. for fresh Therefore LastChangeTime).
    pre_fetch_body is also rendered as a Jinja2 template before being sent.
    Returns the rendered bytes ready to POST.
    Raises TemplateError on render failure.
    """
    context = build_template_context(job)
    if pre_fetch_url:
        rendered_url = Template(pre_fetch_url).render(**context)
        rendered_body = Template(pre_fetch_body).render(**context) if pre_fetch_body else None
        context["fetched"] = await fetch_pre_fetch_context(
            rendered_url, pre_fetch_headers, method=pre_fetch_method, body=rendered_body
        )
    else:
        context["fetched"] = {}
    rendered = Template(template_str).render(**context)
    return rendered.encode()
