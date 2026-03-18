"""
Watches the input directory for new files and auto-submits them as jobs.
Runs as a background asyncio task.
"""
import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path

from app.config import settings, get_runtime_value
from app.database import AsyncSessionLocal
from app.models.job import Job, JobStatus
from app.utils.file_utils import compute_sha256

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}


async def _hash_known_to_db(file_hash: str) -> bool:
    """Return True if we already have a job for this file hash."""
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Job.id).where(Job.file_hash == file_hash).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def _submit_file(file_path: str) -> None:
    from app.config import load_runtime_config

    config = load_runtime_config()
    level = config.get("default_redaction_level", settings.default_redaction_level)
    output_mode = config.get("default_output_mode", settings.default_output_mode)

    file_hash = compute_sha256(file_path)
    if await _hash_known_to_db(file_hash):
        logger.debug(f"Skipping already-processed file: {file_path}")
        return

    filename = Path(file_path).name
    job_id = str(uuid.uuid4())

    # Move to a stable temp location under originals so the input dir can be cleared
    from app.utils.file_utils import get_original_path
    stable_path = str(Path(settings.originals_dir) / f"polled_{job_id[:8]}_{filename}")
    os.makedirs(settings.originals_dir, exist_ok=True)
    shutil.copy2(file_path, stable_path)

    async with AsyncSessionLocal() as session:
        job = Job(
            id=job_id,
            filename=filename,
            file_hash=file_hash,
            file_size_bytes=os.path.getsize(file_path),
            source="poller",
            status=JobStatus.QUEUED,
            level=level,
            output_mode=output_mode,
            input_path=stable_path,
        )
        session.add(job)
        await session.commit()

    # Remove from input dir after copying
    try:
        os.remove(file_path)
    except Exception as e:
        logger.warning(f"Could not remove polled file {file_path}: {e}")

    logger.info(f"Polled file submitted as job {job_id}: {filename}")


async def start_poller():
    """Polls input directory at configured interval."""
    logger.info("Folder poller started.")
    while True:
        try:
            from app.config import load_runtime_config
            config = load_runtime_config()
            enabled = config.get("folder_polling_enabled", settings.folder_polling_enabled)
            interval = int(config.get("poll_interval_seconds", settings.poll_interval_seconds))

            if enabled:
                input_dir = Path(settings.input_dir)
                input_dir.mkdir(parents=True, exist_ok=True)

                for entry in input_dir.iterdir():
                    if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                        await _submit_file(str(entry))

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Poller error: {e}")

        await asyncio.sleep(interval if 'interval' in locals() else 15)

    logger.info("Folder poller stopped.")
