"""
Async worker that picks queued jobs from the database and processes them.
Runs as a background asyncio task within the FastAPI process.
"""
import asyncio
import logging
import os
import shutil
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings, get_runtime_value
from app.database import AsyncSessionLocal
from app.models.job import Job, JobStatus
from app.utils.file_utils import get_output_path, get_original_path, safe_delete
from app.utils.webhook_sender import send_webhook, build_job_payload, render_webhook_template

logger = logging.getLogger(__name__)

# Semaphore set after startup based on runtime config
_semaphore: asyncio.Semaphore = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        concurrency = get_runtime_value("worker_concurrency", settings.worker_concurrency)
        _semaphore = asyncio.Semaphore(int(concurrency))
    return _semaphore


async def _get_next_queued_job() -> Job | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Job)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()


async def _process_job(job_id: str) -> None:
    from app.core.file_router import process_document

    async with _get_semaphore():
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job or job.status != JobStatus.QUEUED:
                return

            job.status = JobStatus.PROCESSING
            job.started_at = datetime.now(timezone.utc)
            await session.commit()

        try:
            config = {}
            try:
                from app.config import load_runtime_config
                config = load_runtime_config()
            except Exception:
                pass

            redaction_color = tuple(config.get("redaction_color", [0, 0, 0]))
            ocr_language = config.get("ocr_language", "eng")
            retain = config.get("retain_originals", settings.retain_originals)

            output_path = get_output_path(job_id, job.filename)

            # Save original if configured
            original_path = None
            if retain and job.input_path and os.path.exists(job.input_path):
                original_path = get_original_path(job_id, job.filename)
                shutil.copy2(job.input_path, original_path)

            t_start = datetime.now(timezone.utc)

            stats = process_document(
                input_path=job.input_path,
                output_path=output_path,
                level=job.level,
                custom_entities=job.custom_entities,
                redaction_color=redaction_color,
                ocr_language=ocr_language,
            )

            t_end = datetime.now(timezone.utc)
            processing_ms = int((t_end - t_start).total_seconds() * 1000)

            # Update job record
            async with AsyncSessionLocal() as session:
                job = await session.get(Job, job_id)
                job.status = JobStatus.COMPLETED
                job.output_path = output_path
                job.original_path = original_path
                job.page_count = stats.get("page_count", 0)
                job.entities_found = stats.get("entities_found", {})
                job.completed_at = t_end
                job.processing_ms = processing_ms
                await session.commit()

            logger.info(
                f"Job {job_id} completed in {processing_ms}ms | "
                f"pages={stats.get('page_count')} entities={sum(stats.get('entities_found', {}).values())}"
            )

            # Deliver webhook if configured
            if job.output_mode == "webhook" and job.webhook_url:
                async with AsyncSessionLocal() as session:
                    job = await session.get(Job, job_id)

                    raw_body = None
                    if job.webhook_template:
                        try:
                            from app.config import load_runtime_config
                            tmpl = load_runtime_config().get("webhook_templates", {}).get(job.webhook_template, {})
                            if tmpl.get("body"):
                                raw_body = render_webhook_template(tmpl["body"], job)
                        except Exception as te:
                            logger.error(f"Job {job_id}: template render failed: {te}")

                    success = await send_webhook(
                        job.webhook_url,
                        build_job_payload(job),
                        secret=job.webhook_secret,
                        extra_headers=job.webhook_headers,
                        raw_body=raw_body,
                    )
                    job.webhook_sent = success
                    await session.commit()

        except Exception as e:
            logger.exception(f"Job {job_id} failed: {e}")
            async with AsyncSessionLocal() as session:
                job = await session.get(Job, job_id)
                if job:
                    job.status = JobStatus.FAILED
                    job.error_message = str(e)
                    job.completed_at = datetime.now(timezone.utc)
                    await session.commit()


async def start_worker():
    """Main worker loop. Polls for queued jobs continuously."""
    logger.info("Job processor worker started.")
    while True:
        try:
            job = await _get_next_queued_job()
            if job:
                asyncio.create_task(_process_job(job.id))
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            await asyncio.sleep(5)
    logger.info("Job processor worker stopped.")
