from sqlalchemy import String, Integer, DateTime, JSON, Enum as SAEnum, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from app.database import Base
import enum
import uuid


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    DETECTING = "detecting"
    PENDING_VALIDATION = "pending_validation"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RedactionLevel(str, enum.Enum):
    MINIMAL = "minimal"
    STANDARD = "standard"
    AGGRESSIVE = "aggressive"
    MAXIMUM = "maximum"
    CUSTOM = "custom"


class OutputMode(str, enum.Enum):
    DIRECTORY = "directory"
    WEBHOOK = "webhook"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String, nullable=False)
    file_hash: Mapped[str] = mapped_column(String, nullable=True, index=True)
    mime_type: Mapped[str] = mapped_column(String, nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String, default="api")  # "api" or "poller"

    status: Mapped[str] = mapped_column(
        SAEnum(JobStatus), default=JobStatus.QUEUED, nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(
        SAEnum(RedactionLevel), default=RedactionLevel.STANDARD, nullable=False
    )
    custom_entities: Mapped[list] = mapped_column(JSON, nullable=True)
    profile_name: Mapped[str] = mapped_column(String, nullable=True)
    output_mode: Mapped[str] = mapped_column(
        SAEnum(OutputMode), default=OutputMode.DIRECTORY, nullable=False
    )
    webhook_url: Mapped[str] = mapped_column(String, nullable=True)
    webhook_headers: Mapped[dict] = mapped_column(JSON, nullable=True)   # per-job custom headers
    webhook_secret: Mapped[str] = mapped_column(String, nullable=True)   # per-job HMAC secret
    webhook_include_file: Mapped[bool] = mapped_column(Boolean, default=False)  # embed base64 file in payload
    webhook_template: Mapped[str] = mapped_column(String, nullable=True)        # named Jinja2 payload template
    webhook_extra: Mapped[dict] = mapped_column(JSON, nullable=True)            # arbitrary vars merged into template context
    webhook_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    input_path: Mapped[str] = mapped_column(String, nullable=True)
    output_path: Mapped[str] = mapped_column(String, nullable=True)
    original_path: Mapped[str] = mapped_column(String, nullable=True)
    custom_output_dir: Mapped[str] = mapped_column(String, nullable=True)  # per-folder output override
    detection_strategy_used: Mapped[str] = mapped_column(String, nullable=True)  # resolved strategy at processing time

    validation_url: Mapped[str] = mapped_column(String, nullable=True)
    completion_callback_url: Mapped[str] = mapped_column(String, nullable=True)
    completion_callback_headers: Mapped[dict] = mapped_column(JSON, nullable=True)
    completion_callback_body: Mapped[str] = mapped_column(String, nullable=True)

    page_count: Mapped[int] = mapped_column(Integer, nullable=True)
    entities_found: Mapped[dict] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    processing_ms: Mapped[int] = mapped_column(Integer, nullable=True)
