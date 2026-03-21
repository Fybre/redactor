import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.api.router import router
from app.core.presidio_engine import get_analyzer

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Redactor service...")
    await init_db()

    # Pre-load Presidio (spaCy model) so first request isn't slow
    try:
        from app.core.presidio_engine import load_custom_recognizers
        from app.config import load_runtime_config
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, get_analyzer)
        custom = load_runtime_config().get("custom_recognizers", [])
        if custom:
            load_custom_recognizers(custom)
    except Exception as e:
        logger.warning(f"Could not pre-load Presidio: {e}")

    from app.workers.job_processor import start_worker
    from app.workers.folder_poller import start_poller

    worker_task = asyncio.create_task(start_worker())
    poller_task = asyncio.create_task(start_poller())

    logger.info("Redactor service ready.")
    yield

    worker_task.cancel()
    poller_task.cancel()
    try:
        await asyncio.gather(worker_task, poller_task, return_exceptions=True)
    except Exception:
        pass
    logger.info("Redactor service stopped.")


app = FastAPI(
    title="Document Redactor",
    description="PII redaction service for PDFs and images",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
