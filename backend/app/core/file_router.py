"""
Routes documents to the appropriate redaction pipeline based on type and content.
"""
import logging
import mimetypes
import os
from pathlib import Path
from typing import Dict, Any, Optional

import fitz

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
PDF_EXTENSION = ".pdf"


def _detect_mime(path: str) -> str:
    try:
        import magic
        return magic.from_file(path, mime=True)
    except Exception:
        mime, _ = mimetypes.guess_type(path)
        return mime or "application/octet-stream"


def _pdf_has_text_layer(pdf_path: str, sample_pages: int = 3) -> bool:
    """
    Returns True if the PDF has a meaningful text layer on at least
    one of the sampled pages.
    """
    try:
        doc = fitz.open(pdf_path)
        pages_to_check = min(sample_pages, doc.page_count)
        for i in range(pages_to_check):
            page = doc[i]
            text = page.get_text("text").strip()
            area = page.rect.width * page.rect.height
            if area > 0 and len(text) / area > 0.0005:
                doc.close()
                return True
        doc.close()
        return False
    except Exception as e:
        logger.warning(f"Could not inspect PDF text layer: {e}")
        return False


def detect_document(
    input_path: str,
    level: str,
    custom_entities: Optional[list] = None,
    ocr_language: str = "eng",
    strategy: str = "presidio",
    llm_base_url: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    min_confidence: float = 0.0,
) -> Dict[str, Any]:
    """
    Detect PII regions without applying redaction. Returns stats + regions list.
    """
    from app.core.pdf_redactor import redact_pdf
    from app.core.image_redactor import detect_image_file

    ext = Path(input_path).suffix.lower()
    mime = _detect_mime(input_path)

    logger.info(f"Detecting {os.path.basename(input_path)} | ext={ext} mime={mime} level={level} strategy={strategy}")

    if ext == PDF_EXTENSION or mime == "application/pdf":
        return redact_pdf(
            input_path, None, level, custom_entities, (0, 0, 0), ocr_language,
            strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
            detect_only=True, min_confidence=min_confidence,
        )
    elif ext in IMAGE_EXTENSIONS or mime.startswith("image/"):
        return detect_image_file(
            input_path, level, custom_entities, ocr_language,
            strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
            min_confidence=min_confidence,
        )
    else:
        raise ValueError(f"Unsupported file type: ext={ext}, mime={mime}")


def apply_document_regions(
    input_path: str,
    output_path: str,
    regions: list,
    redaction_color: tuple = (0, 0, 0),
) -> Dict[str, Any]:
    """
    Apply pre-detected normalised regions to a document without re-running detection.
    """
    from app.core.pdf_redactor import apply_regions_to_pdf
    from app.core.image_redactor import apply_regions_to_image

    ext = Path(input_path).suffix.lower()
    mime = _detect_mime(input_path)

    if ext == PDF_EXTENSION or mime == "application/pdf":
        return apply_regions_to_pdf(input_path, output_path, regions, redaction_color)
    elif ext in IMAGE_EXTENSIONS or mime.startswith("image/"):
        return apply_regions_to_image(input_path, output_path, regions, redaction_color)
    else:
        raise ValueError(f"Unsupported file type: ext={ext}, mime={mime}")


def process_document(
    input_path: str,
    output_path: str,
    level: str,
    custom_entities: Optional[list] = None,
    redaction_color: tuple = (0, 0, 0),
    ocr_language: str = "eng",
    strategy: str = "presidio",
    llm_base_url: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    min_confidence: float = 0.0,
) -> Dict[str, Any]:
    """
    Auto-detect document type and route to the correct redactor.
    Returns stats dict: {page_count, entities_found}.
    """
    from app.core.pdf_redactor import redact_pdf
    from app.core.image_redactor import redact_image_file

    ext = Path(input_path).suffix.lower()
    mime = _detect_mime(input_path)

    logger.info(f"Processing {os.path.basename(input_path)} | ext={ext} mime={mime} level={level} strategy={strategy}")

    if ext == PDF_EXTENSION or mime == "application/pdf":
        # For PDFs, redact_pdf handles both text-layer pages and scanned pages per-page
        return redact_pdf(
            input_path, output_path, level, custom_entities, redaction_color, ocr_language,
            strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
            min_confidence=min_confidence,
        )
    elif ext in IMAGE_EXTENSIONS or mime.startswith("image/"):
        return redact_image_file(
            input_path, output_path, level, custom_entities, redaction_color, ocr_language,
            strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
            min_confidence=min_confidence,
        )
    else:
        raise ValueError(f"Unsupported file type: ext={ext}, mime={mime}")
