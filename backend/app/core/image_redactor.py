"""
OCR-based redaction for images and scanned PDF pages.

Pipeline:
1. Convert image/page to PIL Image.
2. Run pytesseract image_to_data to get word bounding boxes.
3. Reconstruct full text with char-offset → bbox mapping.
4. Run Presidio on the full text.
5. Draw filled black rectangles over PII word bboxes.
6. For PDF pages: replace page content with redacted image pixmap.
"""
import logging
import io
from collections import defaultdict
from typing import Dict, Any, List, Tuple, Optional
from PIL import Image, ImageDraw
import pytesseract
from pytesseract import Output
import fitz  # PyMuPDF

from app.core.presidio_engine import analyze_text

logger = logging.getLogger(__name__)


def _build_ocr_char_map(ocr_data: dict) -> Tuple[str, List[Tuple[int, int, tuple]]]:
    """
    Reconstruct full text and build (char_start, char_end, (left, top, right, bottom)) mapping
    from pytesseract image_to_data output.
    """
    mapping = []
    parts = []
    offset = 0

    for i, word in enumerate(ocr_data["text"]):
        if not word.strip():
            continue
        conf = int(ocr_data["conf"][i])
        if conf < 30:
            continue

        left = ocr_data["left"][i]
        top = ocr_data["top"][i]
        width = ocr_data["width"][i]
        height = ocr_data["height"][i]
        bbox = (left, top, left + width, top + height)

        char_end = offset + len(word)
        mapping.append((offset, char_end, bbox))
        parts.append(word)
        offset = char_end + 1  # space

    return " ".join(parts), mapping


def _rects_for_span(char_start: int, char_end: int, mapping: list) -> List[tuple]:
    return [bbox for w_start, w_end, bbox in mapping if w_start < char_end and w_end > char_start]


def _merge_bboxes(bboxes: List[tuple]) -> tuple:
    left = min(b[0] for b in bboxes)
    top = min(b[1] for b in bboxes)
    right = max(b[2] for b in bboxes)
    bottom = max(b[3] for b in bboxes)
    return (left - 2, top - 2, right + 2, bottom + 2)


def redact_image_file(
    input_path: str,
    output_path: str,
    level: str,
    custom_entities: list = None,
    redaction_color: tuple = (0, 0, 0),
    ocr_language: str = "eng",
) -> Dict[str, Any]:
    """Redact a standalone image file (PNG/JPG/TIFF)."""
    img = Image.open(input_path).convert("RGB")
    entities_found: Dict[str, int] = defaultdict(int)

    redacted = _redact_pil_image(img, level, custom_entities, redaction_color, ocr_language, entities_found)
    redacted.save(output_path)

    return {"page_count": 1, "entities_found": dict(entities_found)}


def _redact_pil_image(
    img: Image.Image,
    level: str,
    custom_entities: Optional[list],
    redaction_color: tuple,
    ocr_language: str,
    entities_counter: dict,
) -> Image.Image:
    """Apply OCR-based PII redaction to a PIL Image. Returns modified copy."""
    try:
        ocr_data = pytesseract.image_to_data(img, lang=ocr_language, output_type=Output.DICT)
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return img

    full_text, char_map = _build_ocr_char_map(ocr_data)
    if not full_text.strip():
        return img

    pii_results = analyze_text(full_text, level, custom_entities)
    if not pii_results:
        return img

    draw = ImageDraw.Draw(img)
    for result in pii_results:
        entities_counter[result.entity_type] = entities_counter.get(result.entity_type, 0) + 1
        bboxes = _rects_for_span(result.start, result.end, char_map)
        if bboxes:
            merged = _merge_bboxes(bboxes)
            draw.rectangle(merged, fill=redaction_color)

    return img


def redact_image_page(
    page: fitz.Page,
    level: str,
    custom_entities: Optional[list],
    redaction_color: tuple,
    ocr_language: str,
    entities_counter: dict,
) -> None:
    """
    Redact a PyMuPDF page that has no text layer (scanned).
    Renders the page to a PIL Image, applies OCR redaction, then replaces
    the page contents with the redacted image.
    """
    # Render page at 300 DPI for good OCR accuracy
    mat = fitz.Matrix(300 / 72, 300 / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    redacted = _redact_pil_image(img, level, custom_entities, redaction_color, ocr_language, entities_counter)

    # Convert back to fitz Pixmap and replace page content
    img_bytes = io.BytesIO()
    redacted.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    # Clear existing page content and insert redacted image
    page.clean_contents()
    page.insert_image(page.rect, stream=img_bytes.read())


def redact_scanned_pdf(
    input_path: str,
    output_path: str,
    level: str,
    custom_entities: list = None,
    redaction_color: tuple = (0, 0, 0),
    ocr_language: str = "eng",
) -> Dict[str, Any]:
    """Redact a PDF that consists entirely of scanned images (no text layer)."""
    doc = fitz.open(input_path)
    total_entities: Dict[str, int] = defaultdict(int)

    for page in doc:
        redact_image_page(page, level, custom_entities, redaction_color, ocr_language, total_entities)

    doc.save(output_path, garbage=4, deflate=True)
    doc.close()

    return {"page_count": doc.page_count, "entities_found": dict(total_entities)}
