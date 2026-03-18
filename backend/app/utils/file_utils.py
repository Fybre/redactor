import hashlib
import os
import shutil
import uuid
from pathlib import Path
from app.config import settings


def compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_temp_path(filename: str) -> str:
    Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix
    return str(Path(settings.temp_dir) / f"{uuid.uuid4()}{suffix}")


def get_output_path(job_id: str, filename: str) -> str:
    Path(settings.output_dir).mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    return str(Path(settings.output_dir) / f"{stem}_redacted_{job_id[:8]}{suffix}")


def get_original_path(job_id: str, filename: str) -> str:
    Path(settings.originals_dir).mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    return str(Path(settings.originals_dir) / f"{stem}_original_{job_id[:8]}{suffix}")


def safe_delete(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def move_to_processed(src: str) -> str:
    """Move a polled file to the originals dir after processing."""
    dest = str(Path(settings.originals_dir) / Path(src).name)
    shutil.move(src, dest)
    return dest
