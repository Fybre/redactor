from fastapi import APIRouter
from sqlalchemy import select, func
from datetime import datetime, timezone, timedelta

from app.database import AsyncSessionLocal
from app.models.job import Job, JobStatus
from app.models.schemas import StatsResponse

router = APIRouter()


@router.get("/health")
async def health():
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(select(func.count()).select_from(Job))
        db_status = "ok"
    except Exception:
        db_status = "error"
    return {"status": "ok", "db": db_status}


@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    async with AsyncSessionLocal() as session:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        total = (await session.execute(select(func.count()).select_from(Job))).scalar()
        today_count = (await session.execute(
            select(func.count()).select_from(Job).where(Job.created_at >= today)
        )).scalar()
        completed = (await session.execute(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.COMPLETED)
        )).scalar()
        failed = (await session.execute(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.FAILED)
        )).scalar()
        queued = (await session.execute(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
        )).scalar()
        processing = (await session.execute(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.PROCESSING)
        )).scalar()

        # Sum total entities and pages from completed jobs
        rows = (await session.execute(
            select(Job.entities_found, Job.page_count, Job.processing_ms)
            .where(Job.status == JobStatus.COMPLETED)
        )).all()

        total_entities = 0
        total_pages = 0
        total_ms = []
        for row in rows:
            if row.entities_found:
                total_entities += sum(row.entities_found.values())
            if row.page_count:
                total_pages += row.page_count
            if row.processing_ms:
                total_ms.append(row.processing_ms)

        avg_ms = sum(total_ms) / len(total_ms) if total_ms else None

    return StatsResponse(
        total_jobs=total,
        jobs_today=today_count,
        jobs_completed=completed,
        jobs_failed=failed,
        jobs_queued=queued,
        jobs_processing=processing,
        total_entities_found=total_entities,
        total_pages_processed=total_pages,
        avg_processing_ms=avg_ms,
    )
