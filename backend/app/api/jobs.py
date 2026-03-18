import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.job import Job, JobStatus
from app.models.schemas import JobResponse, JobListResponse
from app.utils.file_utils import safe_delete

router = APIRouter()


def _job_to_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        filename=job.filename,
        status=job.status,
        level=job.level,
        output_mode=job.output_mode,
        source=job.source,
        page_count=job.page_count,
        entities_found=job.entities_found,
        error_message=job.error_message,
        webhook_sent=job.webhook_sent,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        processing_ms=job.processing_ms,
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    q = select(Job).order_by(desc(Job.created_at))
    if status:
        q = q.where(Job.status == status)

    total_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(total_q)).scalar()

    q = q.offset((page - 1) * per_page).limit(per_page)
    jobs = (await db.execute(q)).scalars().all()

    return JobListResponse(
        jobs=[_job_to_response(j) for j in jobs],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_response(job)


@router.get("/{job_id}/report")
async def get_job_report(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.id,
        "filename": job.filename,
        "status": job.status,
        "level": job.level,
        "page_count": job.page_count,
        "entities_found": job.entities_found or {},
        "total_entities": sum((job.entities_found or {}).values()),
        "processing_ms": job.processing_ms,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "source": job.source,
    }


@router.get("/{job_id}/download")
async def download_redacted(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail=f"Job is not completed (status: {job.status})")
    if not job.output_path or not os.path.exists(job.output_path):
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(
        job.output_path,
        filename=f"redacted_{job.filename}",
        media_type="application/octet-stream",
    )


@router.get("/{job_id}/view")
async def view_redacted(job_id: str, db: AsyncSession = Depends(get_db)):
    """Serve the redacted file inline so the browser can display it natively."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail=f"Job is not completed (status: {job.status})")
    if not job.output_path or not os.path.exists(job.output_path):
        raise HTTPException(status_code=404, detail="Output file not found")

    # Derive a browser-friendly MIME type from the stored value
    mime = job.mime_type or "application/octet-stream"
    if mime == "application/octet-stream":
        ext = os.path.splitext(job.filename)[1].lower()
        mime = {
            ".pdf":  "application/pdf",
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".tiff": "image/tiff",
            ".tif":  "image/tiff",
            ".bmp":  "image/bmp",
        }.get(ext, "application/octet-stream")

    return FileResponse(
        job.output_path,
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="redacted_{job.filename}"'},
    )


@router.get("/{job_id}/original")
async def download_original(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.original_path or not os.path.exists(job.original_path):
        raise HTTPException(status_code=404, detail="Original file not available")
    return FileResponse(
        job.original_path,
        filename=f"original_{job.filename}",
        media_type="application/octet-stream",
    )


@router.delete("/{job_id}")
async def delete_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status in (JobStatus.PROCESSING,):
        raise HTTPException(status_code=400, detail="Cannot delete a job that is currently processing")

    # Cancel queued job
    if job.status == JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        await db.commit()
        return {"status": "cancelled"}

    # Clean up files
    safe_delete(job.output_path)
    safe_delete(job.input_path)
    safe_delete(job.original_path)

    await db.delete(job)
    await db.commit()
    return {"status": "deleted"}


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="Only failed or cancelled jobs can be retried")

    job.status = JobStatus.QUEUED
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.processing_ms = None
    await db.commit()
    return {"status": "requeued", "job_id": job.id}
