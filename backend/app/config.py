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
    "webhook_templates": {
        "therefore_create_document": {
            "description": "Therefore DMS — CreateDocument with redacted file as base64 stream",
            "headers": {},
            "body": (
                '{\n'
                '  "CategoryNo": {{ category_no | default(57) }},\n'
                '  "CheckInComments": "Redacted by Redactor — {{ filename }}",\n'
                '  "IndexDataItems": [\n'
                '    {\n'
                '      "StringIndexData": {\n'
                '        "FieldName": "Document_Name",\n'
                '        "FieldNo": 0,\n'
                '        "DataValue": "{{ stem }}"\n'
                '      }\n'
                '    },\n'
                '    {\n'
                '      "StringIndexData": {\n'
                '        "FieldName": "Redaction_Level",\n'
                '        "FieldNo": 0,\n'
                '        "DataValue": "{{ level }}"\n'
                '      }\n'
                '    },\n'
                '    {\n'
                '      "StringIndexData": {\n'
                '        "FieldName": "Job_ID",\n'
                '        "FieldNo": 0,\n'
                '        "DataValue": "{{ job_id }}"\n'
                '      }\n'
                '    },\n'
                '    {\n'
                '      "DateIndexData": {\n'
                '        "FieldName": "Redaction_Date",\n'
                '        "FieldNo": 0,\n'
                '        "DataISO8601Value": "{{ completed_at }}"\n'
                '      }\n'
                '    },\n'
                '    {\n'
                '      "IntIndexData": {\n'
                '        "FieldName": "Page_Count",\n'
                '        "FieldNo": 0,\n'
                '        "DataValue": {{ page_count }}\n'
                '      }\n'
                '    }\n'
                '  ],\n'
                '  "Streams": [\n'
                '    {% if file_data %}\n'
                '    {\n'
                '      "FileName": "{{ file_name }}",\n'
                '      "FileDataBase64JSON": "{{ file_data }}",\n'
                '      "NewStreamInsertMode": 0\n'
                '    }\n'
                '    {% endif %}\n'
                '  ],\n'
                '  "DoFillDependentFields": true\n'
                '}'
            ),
        },
        "therefore_update_document": {
            "description": "Therefore UpdateDocument — replace primary stream with redacted file",
            "headers": {},
            "body": (
                '{\n'
                '  "DocNo": {{ doc_no }},\n'
                '  "CheckInComments": "Redacted by Redactor — {{ filename }}",\n'
                '  "LastChangeTime": "{{ completed_at }}",\n'
                '  "DoFillDependentFields": false,\n'
                '  "StreamsToUpdate": [\n'
                '    {% if file_data %}\n'
                '    {\n'
                '      "StreamNo": {{ stream_no | default(0) }},\n'
                '      "FileName": "{{ file_name }}",\n'
                '      "FileData": "{{ file_data }}"\n'
                '    }\n'
                '    {% endif %}\n'
                '  ]\n'
                '}'
            ),
        },
        "therefore_update_document2": {
            "description": "Therefore UpdateDocument2 — replace primary stream with redacted file (v2 endpoint)",
            "headers": {},
            "body": (
                '{\n'
                '  "DocNo": {{ doc_no }},\n'
                '  "CheckInComments": "Redacted by Redactor — {{ filename }}",\n'
                '  "LastChangeTime": "{{ completed_at }}",\n'
                '  "DoFillDependentFields": false,\n'
                '  "StreamsToUpdate": [\n'
                '    {% if file_data %}\n'
                '    {\n'
                '      "StreamNo": {{ stream_no | default(0) }},\n'
                '      "FileName": "{{ file_name }}",\n'
                '      "FileData": "{{ file_data }}",\n'
                '      "NewStreamInsertMode": {{ insert_mode | default(2) }}\n'
                '    }\n'
                '    {% endif %}\n'
                '  ]\n'
                '}'
            ),
        },
    },
}


def load_runtime_config() -> dict:
    if _RUNTIME_CONFIG_PATH.exists():
        try:
            with open(_RUNTIME_CONFIG_PATH) as f:
                saved = json.load(f)
            config = {**_DEFAULT_RUNTIME_CONFIG, **saved}
            # Merge dict-type defaults additively so new default templates/profiles
            # are visible on existing deployments that pre-date them
            for key, default_val in _DEFAULT_RUNTIME_CONFIG.items():
                if isinstance(default_val, dict) and key not in saved:
                    config[key] = default_val
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
