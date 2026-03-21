"""
Watches configured input folders for new files and auto-submits them as jobs.
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
from app.models.job import Job, JobStatus, RedactionLevel
from app.utils.file_utils import compute_sha256

_BUILTIN_LEVELS = {l.value for l in RedactionLevel if l != RedactionLevel.CUSTOM}

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


async def _submit_file(
    file_path: str,
    profile: str = None,
    custom_output_dir: str = None,
) -> None:
    from app.config import load_runtime_config

    config = load_runtime_config()
    level = config.get("default_redaction_level", settings.default_redaction_level)
    output_mode = config.get("default_output_mode", settings.default_output_mode)

    # Resolve profile → level / custom_entities
    custom_entities = None
    if profile:
        if profile in _BUILTIN_LEVELS:
            # It's a built-in redaction level name (minimal, standard, aggressive, maximum)
            level = profile
            profile = None  # don't store as profile_name
        else:
            profiles = config.get("profiles", {})
            if profile in profiles:
                custom_entities = profiles[profile].get("entities", [])
                level = "custom"
            else:
                logger.warning(f"Watched folder profile '{profile}' not found — using default level")
                profile = None

    file_hash = compute_sha256(file_path)
    if await _hash_known_to_db(file_hash):
        logger.debug(f"Skipping already-processed file: {file_path}")
        return

    filename = Path(file_path).name
    job_id = str(uuid.uuid4())

    # Move to a stable temp location under originals so the input dir can be cleared
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
            custom_entities=custom_entities,
            profile_name=profile,
            custom_output_dir=custom_output_dir or None,
        )
        session.add(job)
        await session.commit()

    # Remove from input dir after copying
    try:
        os.remove(file_path)
    except Exception as e:
        logger.warning(f"Could not remove polled file {file_path}: {e}")

    logger.info(f"Polled file submitted as job {job_id}: {filename}" +
                (f" (profile: {profile})" if profile else "") +
                (f" (output: {custom_output_dir})" if custom_output_dir else ""))


async def start_poller():
    """Polls watched folders at configured interval."""
    logger.info("Folder poller started.")
    while True:
        try:
            from app.config import load_runtime_config
            config = load_runtime_config()
            enabled = config.get("folder_polling_enabled", settings.folder_polling_enabled)
            interval = int(config.get("poll_interval_seconds", settings.poll_interval_seconds))

            if enabled:
                watched = config.get("watched_folders", [])
                # Fall back to the single default input_dir if no watched folders configured
                if not watched:
                    watched = [{"path": settings.input_dir, "enabled": True}]

                for folder in watched:
                    if not folder.get("enabled", True):
                        continue
                    folder_path = Path(folder.get("path", settings.input_dir))
                    profile = folder.get("profile") or None
                    output_path = folder.get("output_path") or None

                    try:
                        folder_path.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        logger.warning(f"Cannot create watched folder {folder_path}: {e}")
                        continue

                    for entry in folder_path.iterdir():
                        if entry.is_file() and not entry.name.startswith('.') and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                            await _submit_file(str(entry), profile=profile, custom_output_dir=output_path)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Poller error: {e}")

        await asyncio.sleep(interval if 'interval' in locals() else 15)

    logger.info("Folder poller stopped.")
