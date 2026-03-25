"""
Validation workflow endpoints.

GET  /jobs/{id}/preview/{page}  — PNG render of input page at 150 DPI
GET  /jobs/{id}/regions         — List detected + user regions
PUT  /jobs/{id}/regions         — Bulk update statuses; upsert user-drawn regions
POST /jobs/{id}/apply           — Apply approved regions, deliver webhook + callback
"""
import io
import logging
import os
from pathlib import Path
from typing import List, Optional

import fitz
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.job import Job, JobStatus
from app.models.region import RedactionRegion

router = APIRouter()
logger = logging.getLogger(__name__)

_PREVIEW_DPI = 150   # DPI for rendering PDF pages as preview images in the validation UI


# ── Preview ──────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/preview/{page_num}")
async def get_preview(job_id: str, page_num: int, db: AsyncSession = Depends(get_db)):
    """Render a page of the original input document as PNG (no redaction applied)."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.input_path or not os.path.exists(job.input_path):
        raise HTTPException(status_code=404, detail="Input file not found")

    ext = Path(job.input_path).suffix.lower()

    if ext == ".pdf":
        doc = fitz.open(job.input_path)
        if page_num >= doc.page_count:
            doc.close()
            raise HTTPException(status_code=404, detail="Page not found")
        mat = fitz.Matrix(_PREVIEW_DPI / 72, _PREVIEW_DPI / 72)
        pix = doc[page_num].get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
    else:
        if page_num != 0:
            raise HTTPException(status_code=404, detail="Page not found")
        img = Image.open(job.input_path).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "max-age=3600"},
    )


# ── Regions ───────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/regions")
async def get_regions(job_id: str, db: AsyncSession = Depends(get_db)):
    """Return all detected and user-drawn regions for a job."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await db.execute(
        select(RedactionRegion)
        .where(RedactionRegion.job_id == job_id)
        .order_by(RedactionRegion.page, RedactionRegion.id)
    )
    regions = result.scalars().all()

    return {
        "regions": [
            {
                "id": r.id,
                "page": r.page,
                "x0": r.x0,
                "y0": r.y0,
                "x1": r.x1,
                "y1": r.y1,
                "entity_type": r.entity_type,
                "original_text": r.original_text,
                "score": r.score,
                "source": r.source,
                "status": r.status,
            }
            for r in regions
        ]
    }


class RegionUpdate(BaseModel):
    id: Optional[int] = None
    status: Optional[str] = None
    page: Optional[int] = None
    x0: Optional[float] = None
    y0: Optional[float] = None
    x1: Optional[float] = None
    y1: Optional[float] = None
    entity_type: Optional[str] = None
    source: Optional[str] = None
    original_text: Optional[str] = None


class RegionsBulkUpdate(BaseModel):
    regions: List[RegionUpdate]


@router.put("/jobs/{job_id}/regions")
async def update_regions(
    job_id: str, body: RegionsBulkUpdate, db: AsyncSession = Depends(get_db)
):
    """Bulk update region statuses and/or insert user-drawn regions."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    for r in body.regions:
        if r.id is not None:
            region = await db.get(RedactionRegion, r.id)
            if region and region.job_id == job_id:
                if r.status is not None:
                    region.status = r.status
        else:
            new_region = RedactionRegion(
                job_id=job_id,
                page=r.page if r.page is not None else 0,
                x0=r.x0 or 0.0,
                y0=r.y0 or 0.0,
                x1=r.x1 or 0.0,
                y1=r.y1 or 0.0,
                entity_type=r.entity_type or "USER_DEFINED",
                original_text=r.original_text,
                score=1.0,
                source=r.source or "user",
                status=r.status or "approved",
            )
            db.add(new_region)

    await db.commit()
    return {"ok": True}


# ── Apply ─────────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/apply")
async def apply_validation(job_id: str, db: AsyncSession = Depends(get_db)):
    """Apply approved regions, deliver file webhook, fire completion callback."""
    from app.workers.job_processor import run_validation_job

    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.PENDING_VALIDATION:
        raise HTTPException(
            status_code=400,
            detail=f"Job status is {job.status}, expected pending_validation",
        )

    # Promote any remaining pending regions to approved — Save & Apply means
    # "redact everything I haven't explicitly rejected".
    pending_result = await db.execute(
        select(RedactionRegion)
        .where(RedactionRegion.job_id == job_id, RedactionRegion.status == "pending")
    )
    for region in pending_result.scalars().all():
        region.status = "approved"

    job.status = JobStatus.PROCESSING
    await db.commit()

    await run_validation_job(job_id)

    await db.refresh(job)
    if job.status == JobStatus.FAILED:
        raise HTTPException(status_code=500, detail=job.error_message or "Apply failed")

    return {"status": job.status, "job_id": job_id}
