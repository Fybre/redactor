"""
Periodic cleanup task: enforces retention_days and removes orphaned temp files.
Runs as a background asyncio task.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.job import Job, JobStatus
from app.utils.file_utils import safe_delete

logger = logging.getLogger(__name__)

_CLEANUP_INTERVAL_SECONDS = 3600  # run once per hour


async def _purge_old_jobs(retention_days: int) -> None:
    if retention_days <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Job).where(
                Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]),
                Job.completed_at < cutoff,
            )
        )
        jobs = result.scalars().all()
        if not jobs:
            return
        for job in jobs:
            safe_delete(job.output_path)
            safe_delete(job.original_path)
            safe_delete(job.input_path)
            await session.delete(job)
        await session.commit()
        logger.info(f"Retention cleanup: purged {len(jobs)} jobs older than {retention_days} days")


async def _purge_orphaned_temp_files() -> None:
    """Delete polled_* staging files in temp_dir whose job is completed or failed."""
    temp_dir = Path(settings.temp_dir)
    if not temp_dir.exists():
        return

    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Job.input_path).where(
                Job.source == "poller",
                Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]),
                Job.input_path.isnot(None),
            )
        )
        paths = {row[0] for row in result.all()}

    removed = 0
    for entry in temp_dir.iterdir():
        if entry.name.startswith("polled_") and str(entry) in paths:
            try:
                entry.unlink()
                removed += 1
            except Exception as e:
                logger.warning(f"Could not delete orphaned temp file {entry}: {e}")

    if removed:
        logger.info(f"Temp cleanup: removed {removed} orphaned polled staging files")


async def start_cleanup():
    """Periodic cleanup loop."""
    logger.info("Cleanup task started.")
    while True:
        try:
            from app.config import load_runtime_config
            config = load_runtime_config()
            retention_days = int(config.get("retention_days", settings.retention_days))
            await _purge_old_jobs(retention_days)
            await _purge_orphaned_temp_files()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)

    logger.info("Cleanup task stopped.")
