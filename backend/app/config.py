from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import json
import os
from pathlib import Path


class Settings(BaseSettings):
    # Paths
    database_url: str = "sqlite+aiosqlite:////data/redactor.db"
    input_dir: str = "/data/input"
    output_dir: str = "/data/output"
    originals_dir: str = "/data/originals"
    temp_dir: str = "/data/temp"

    # Worker
    worker_concurrency: int = 2
    max_file_size_mb: int = 100

    # Polling
    folder_polling_enabled: bool = True
    poll_interval_seconds: int = 15

    # Defaults
    default_redaction_level: str = "standard"
    default_output_mode: str = "directory"
    retain_originals: bool = True
    retention_days: int = 30

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

# Runtime config stored in a JSON file so the web UI can update it without restart
_RUNTIME_CONFIG_PATH = Path("/data/runtime_config.json")

_DEFAULT_RUNTIME_CONFIG = {
    "folder_polling_enabled": settings.folder_polling_enabled,
    "poll_interval_seconds": settings.poll_interval_seconds,
    "default_redaction_level": settings.default_redaction_level,
    "default_output_mode": settings.default_output_mode,
    "retain_originals": settings.retain_originals,
    "retention_days": settings.retention_days,
    "worker_concurrency": settings.worker_concurrency,
    "max_file_size_mb": settings.max_file_size_mb,
    "redaction_color": [0, 0, 0],
    "ocr_language": "eng",
    "allowed_extensions": ["pdf", "png", "jpg", "jpeg", "tiff", "tif"],
    "webhooks": [],
    "profiles": {},
    "default_profile": None,
}


def load_runtime_config() -> dict:
    if _RUNTIME_CONFIG_PATH.exists():
        try:
            with open(_RUNTIME_CONFIG_PATH) as f:
                saved = json.load(f)
            config = {**_DEFAULT_RUNTIME_CONFIG, **saved}
            return config
        except Exception:
            pass
    return dict(_DEFAULT_RUNTIME_CONFIG)


def save_runtime_config(config: dict) -> None:
    _RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_RUNTIME_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def get_runtime_value(key: str, default=None):
    return load_runtime_config().get(key, default)
