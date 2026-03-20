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

from app.config import settings, get_runtime_value, load_runtime_config
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


async def _run_job(job_id: str) -> None:
    """
    Core processing logic. Assumes the job record already has status=PROCESSING.
    Handles redaction, DB update, and webhook/output delivery.
    """
    from app.core.file_router import process_document

    # Load job — read filename/paths before the blocking process_document call
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job:
            return
        job_filename = job.filename
        job_input_path = job.input_path
        job_level = job.level
        job_custom_entities = job.custom_entities
        job_output_mode = job.output_mode
        job_webhook_url = job.webhook_url
        job_webhook_secret = job.webhook_secret
        job_webhook_headers = job.webhook_headers
        job_webhook_template = job.webhook_template
        job_webhook_include_file = job.webhook_include_file
        job_webhook_extra = job.webhook_extra

    try:
        config = {}
        try:
            config = load_runtime_config()
        except Exception:
            pass

        redaction_color = tuple(config.get("redaction_color", [0, 0, 0]))
        ocr_language = config.get("ocr_language", "eng")
        retain = config.get("retain_originals", settings.retain_originals)
        detection_strategy = config.get("detection_strategy", "presidio")
        llm_base_url = config.get("llm_base_url", "http://ollama:11434/v1")
        llm_model = config.get("llm_model", "llama3.2:3b")
        llm_api_key = config.get("llm_api_key", "ollama")

        output_path = get_output_path(job_id, job_filename)

        # Save original if configured
        original_path = None
        if retain and job_input_path and os.path.exists(job_input_path):
            original_path = get_original_path(job_id, job_filename)
            shutil.copy2(job_input_path, original_path)

        t_start = datetime.now(timezone.utc)

        stats = process_document(
            input_path=job_input_path,
            output_path=output_path,
            level=job_level,
            custom_entities=job_custom_entities,
            redaction_color=redaction_color,
            ocr_language=ocr_language,
            strategy=detection_strategy,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
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
        if job_output_mode == "webhook" and job_webhook_url:
            async with AsyncSessionLocal() as session:
                job = await session.get(Job, job_id)

                raw_body = None
                template_headers = None
                if job_webhook_template:
                    try:
                        tmpl = config.get("webhook_templates", {}).get(job_webhook_template, {})
                        if tmpl.get("body"):
                            raw_body = render_webhook_template(tmpl["body"], job)
                        if tmpl.get("headers"):
                            template_headers = {**(job_webhook_headers or {}), **tmpl["headers"]}
                    except Exception as te:
                        logger.error(f"Job {job_id}: template render failed: {te}")

                success = await send_webhook(
                    job_webhook_url,
                    build_job_payload(job),
                    secret=job_webhook_secret,
                    extra_headers=template_headers or job_webhook_headers,
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


async def _process_job(job_id: str) -> None:
    """Worker path: claim a QUEUED job and process it."""
    async with _get_semaphore():
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if not job or job.status != JobStatus.QUEUED:
                return
            job.status = JobStatus.PROCESSING
            job.started_at = datetime.now(timezone.utc)
            await session.commit()

        await _run_job(job_id)


async def process_job_now(job_id: str) -> None:
    """
    Synchronous-upload path: job is already marked PROCESSING by the caller.
    Respects the concurrency semaphore so sync jobs don't bypass worker limits.
    """
    async with _get_semaphore():
        await _run_job(job_id)


async def run_detection_job(job_id: str) -> None:
    """
    Validation workflow — detection pass only.
    Runs PII detection, persists RedactionRegion rows, then sets job to PENDING_VALIDATION.
    """
    from app.core.file_router import detect_document
    from app.models.region import RedactionRegion

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job:
            return
        job_input_path = job.input_path
        job_level = job.level
        job_custom_entities = job.custom_entities

    try:
        config = {}
        try:
            config = load_runtime_config()
        except Exception:
            pass

        ocr_language = config.get("ocr_language", "eng")
        detection_strategy = config.get("detection_strategy", "presidio")
        llm_base_url = config.get("llm_base_url", "http://ollama:11434/v1")
        llm_model = config.get("llm_model", "llama3.2:3b")
        llm_api_key = config.get("llm_api_key", "ollama")
        auto_approve_threshold = float(config.get("auto_approve_threshold", 0.85))

        result = detect_document(
            input_path=job_input_path,
            level=job_level,
            custom_entities=job_custom_entities,
            ocr_language=ocr_language,
            strategy=detection_strategy,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
        )

        regions = result.get("regions", [])

        async with AsyncSessionLocal() as session:
            for r in regions:
                score = r.get("score", 1.0)
                status = "auto_approved" if score >= auto_approve_threshold else "pending"
                session.add(RedactionRegion(
                    job_id=job_id,
                    page=r["page"],
                    x0=r["x0"],
                    y0=r["y0"],
                    x1=r["x1"],
                    y1=r["y1"],
                    entity_type=r["entity_type"],
                    original_text=r.get("original_text"),
                    score=score,
                    source="auto",
                    status=status,
                ))

            job = await session.get(Job, job_id)
            job.status = JobStatus.PENDING_VALIDATION
            job.page_count = result.get("page_count", 0)
            job.entities_found = result.get("entities_found", {})
            await session.commit()

        logger.info(f"Detection job {job_id} complete: {len(regions)} regions on {result.get('page_count')} page(s)")

    except Exception as e:
        logger.exception(f"Detection job {job_id} failed: {e}")
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                job.completed_at = datetime.now(timezone.utc)
                await session.commit()


async def run_validation_job(job_id: str) -> None:
    """
    Validation workflow — apply pass.
    Reads approved regions from DB, applies them, delivers file webhook and callback.
    Job must already be in PROCESSING state when this is called.
    """
    from app.core.file_router import apply_document_regions
    from app.models.region import RedactionRegion
    from sqlalchemy import select as _select

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job:
            return
        job_filename = job.filename
        job_input_path = job.input_path
        job_output_mode = job.output_mode
        job_webhook_url = job.webhook_url
        job_webhook_secret = job.webhook_secret
        job_webhook_headers = job.webhook_headers
        job_webhook_template = job.webhook_template
        job_webhook_include_file = job.webhook_include_file
        job_webhook_extra = job.webhook_extra
        completion_callback_url = job.completion_callback_url
        completion_callback_headers = job.completion_callback_headers
        completion_callback_body = job.completion_callback_body

        result = await session.execute(
            _select(RedactionRegion)
            .where(RedactionRegion.job_id == job_id)
            .where(RedactionRegion.status.in_(["approved", "auto_approved"]))
        )
        approved = result.scalars().all()
        regions_data = [
            {"page": r.page, "x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1,
             "entity_type": r.entity_type}
            for r in approved
        ]
        entities_found: dict = {}
        for r in approved:
            entities_found[r.entity_type] = entities_found.get(r.entity_type, 0) + 1

    try:
        config = {}
        try:
            config = load_runtime_config()
        except Exception:
            pass

        redaction_color = tuple(config.get("redaction_color", [0, 0, 0]))
        retain = config.get("retain_originals", settings.retain_originals)

        output_path = get_output_path(job_id, job_filename)

        original_path = None
        if retain and job_input_path and os.path.exists(job_input_path):
            original_path = get_original_path(job_id, job_filename)
            shutil.copy2(job_input_path, original_path)

        t_start = datetime.now(timezone.utc)

        stats = apply_document_regions(
            input_path=job_input_path,
            output_path=output_path,
            regions=regions_data,
            redaction_color=redaction_color,
        )

        t_end = datetime.now(timezone.utc)
        processing_ms = int((t_end - t_start).total_seconds() * 1000)

        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            job.status = JobStatus.COMPLETED
            job.output_path = output_path
            job.original_path = original_path
            job.page_count = stats.get("page_count", 0)
            job.entities_found = entities_found
            job.completed_at = t_end
            job.processing_ms = processing_ms
            await session.commit()

        logger.info(f"Validation apply job {job_id} completed in {processing_ms}ms | regions={len(regions_data)}")

        # Deliver file webhook if configured
        if job_output_mode == "webhook" and job_webhook_url:
            async with AsyncSessionLocal() as session:
                job = await session.get(Job, job_id)
                raw_body = None
                template_headers = None
                if job_webhook_template:
                    try:
                        tmpl = config.get("webhook_templates", {}).get(job_webhook_template, {})
                        if tmpl.get("body"):
                            raw_body = render_webhook_template(tmpl["body"], job)
                        if tmpl.get("headers"):
                            template_headers = {**(job_webhook_headers or {}), **tmpl["headers"]}
                    except Exception as te:
                        logger.error(f"Job {job_id}: template render failed: {te}")

                success = await send_webhook(
                    job_webhook_url,
                    build_job_payload(job),
                    secret=job_webhook_secret,
                    extra_headers=template_headers or job_webhook_headers,
                    raw_body=raw_body,
                )
                job.webhook_sent = success
                await session.commit()

        # Fire completion callback to release Therefore "waiting" state
        if completion_callback_url:
            import json as _json
            callback_body_parsed = {}
            if completion_callback_body:
                try:
                    callback_body_parsed = _json.loads(completion_callback_body)
                except Exception:
                    callback_body_parsed = {"body": completion_callback_body}
            await send_webhook(
                completion_callback_url,
                callback_body_parsed,
                extra_headers=completion_callback_headers,
            )
            logger.info(f"Job {job_id}: completion callback fired to {completion_callback_url}")

    except Exception as e:
        logger.exception(f"Validation apply job {job_id} failed: {e}")
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
