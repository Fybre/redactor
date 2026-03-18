from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.models.job import JobStatus, RedactionLevel, OutputMode


class JobResponse(BaseModel):
    id: str
    filename: str
    status: str
    level: str
    output_mode: str
    source: str
    page_count: Optional[int]
    entities_found: Optional[Dict[str, int]]
    error_message: Optional[str]
    webhook_sent: Optional[bool]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    processing_ms: Optional[int]

    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    jobs: List[JobResponse]
    total: int
    page: int
    per_page: int


class SystemConfig(BaseModel):
    folder_polling_enabled: bool
    poll_interval_seconds: int
    default_redaction_level: str
    default_output_mode: str
    retain_originals: bool
    retention_days: int
    worker_concurrency: int
    max_file_size_mb: int
    redaction_color: List[int]
    ocr_language: str
    allowed_extensions: List[str]
    webhooks: List[Dict[str, Any]]
    profiles: Dict[str, Any]
    default_profile: Optional[str]


class ProfileCreate(BaseModel):
    name: str
    entities: List[str]
    description: Optional[str] = None


class WebhookConfig(BaseModel):
    url: str
    secret: Optional[str] = ""
    name: Optional[str] = ""
    enabled: bool = True


class StatsResponse(BaseModel):
    total_jobs: int
    jobs_today: int
    jobs_completed: int
    jobs_failed: int
    jobs_queued: int
    jobs_processing: int
    total_entities_found: int
    total_pages_processed: int
    avg_processing_ms: Optional[float]
