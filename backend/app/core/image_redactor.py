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

_OCR_CONFIDENCE_THRESHOLD = 30   # discard words below this pytesseract confidence (0–100)
_BBOX_PADDING_PX = 2             # pixels added around each redaction box for clean coverage
_OCR_DPI = 300                   # DPI used when rendering PDF pages for OCR


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
        if conf < _OCR_CONFIDENCE_THRESHOLD:
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
    return (left - _BBOX_PADDING_PX, top - _BBOX_PADDING_PX, right + _BBOX_PADDING_PX, bottom + _BBOX_PADDING_PX)


def redact_image_file(
    input_path: str,
    output_path: str,
    level: str,
    custom_entities: list = None,
    redaction_color: tuple = (0, 0, 0),
    ocr_language: str = "eng",
    strategy: str = "presidio",
    llm_base_url: str = None,
    llm_model: str = None,
    llm_api_key: str = None,
    min_confidence: float = 0.0,
) -> Dict[str, Any]:
    """Redact a standalone image file (PNG/JPG/TIFF)."""
    img = Image.open(input_path).convert("RGB")
    entities_found: Dict[str, int] = defaultdict(int)

    redacted = _redact_pil_image(
        img, level, custom_entities, redaction_color, ocr_language, entities_found,
        strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
        min_confidence=min_confidence,
    )
    redacted.save(output_path)

    return {"page_count": 1, "entities_found": dict(entities_found)}


def detect_image_file(
    input_path: str,
    level: str,
    custom_entities: list = None,
    ocr_language: str = "eng",
    strategy: str = "presidio",
    llm_base_url: str = None,
    llm_model: str = None,
    llm_api_key: str = None,
    min_confidence: float = 0.0,
) -> Dict[str, Any]:
    """Detect PII regions in a standalone image file without applying redaction."""
    img = Image.open(input_path).convert("RGB")
    regions = _detect_pil_image(
        img, 0, level, custom_entities, ocr_language,
        strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
        min_confidence=min_confidence,
    )
    entities_found: Dict[str, int] = {}
    for r in regions:
        entities_found[r["entity_type"]] = entities_found.get(r["entity_type"], 0) + 1
    return {"page_count": 1, "entities_found": entities_found, "regions": regions}


def apply_regions_to_image(
    input_path: str,
    output_path: str,
    regions: List[dict],
    redaction_color: tuple = (0, 0, 0),
) -> Dict[str, Any]:
    """Apply pre-detected normalised regions to an image file."""
    img = Image.open(input_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    for region in regions:
        x0 = int(region["x0"] * img.width)
        y0 = int(region["y0"] * img.height)
        x1 = int(region["x1"] * img.width)
        y1 = int(region["y1"] * img.height)
        draw.rectangle((x0, y0, x1, y1), fill=redaction_color)
    img.save(output_path)
    return {"page_count": 1, "entities_found": {}}


def _detect_pil_image(
    img: Image.Image,
    page_num: int,
    level: str,
    custom_entities: Optional[list],
    ocr_language: str,
    strategy: str = "presidio",
    llm_base_url: str = None,
    llm_model: str = None,
    llm_api_key: str = None,
    min_confidence: float = 0.0,
) -> List[dict]:
    """Run OCR + Presidio on a PIL Image and return normalised region dicts (no drawing)."""
    try:
        ocr_data = pytesseract.image_to_data(img, lang=ocr_language, output_type=Output.DICT)
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return []

    full_text, char_map = _build_ocr_char_map(ocr_data)
    if not full_text.strip():
        return []

    pii_results = analyze_text(
        full_text, level, custom_entities,
        strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
    )
    if min_confidence > 0:
        pii_results = [r for r in pii_results if getattr(r, "score", 1.0) >= min_confidence]

    regions = []
    for result in pii_results:
        bboxes = _rects_for_span(result.start, result.end, char_map)
        if not bboxes:
            continue
        merged = _merge_bboxes(bboxes)
        regions.append({
            "page": page_num,
            "x0": merged[0] / img.width,
            "y0": merged[1] / img.height,
            "x1": merged[2] / img.width,
            "y1": merged[3] / img.height,
            "entity_type": result.entity_type,
            "original_text": full_text[result.start:result.end],
            "score": getattr(result, "score", 1.0),
        })
    return regions


def _redact_pil_image(
    img: Image.Image,
    level: str,
    custom_entities: Optional[list],
    redaction_color: tuple,
    ocr_language: str,
    entities_counter: dict,
    strategy: str = "presidio",
    llm_base_url: str = None,
    llm_model: str = None,
    llm_api_key: str = None,
    min_confidence: float = 0.0,
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

    pii_results = analyze_text(
        full_text, level, custom_entities,
        strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
    )
    if min_confidence > 0:
        pii_results = [r for r in pii_results if getattr(r, "score", 1.0) >= min_confidence]
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
    strategy: str = "presidio",
    llm_base_url: str = None,
    llm_model: str = None,
    llm_api_key: str = None,
    min_confidence: float = 0.0,
) -> None:
    """
    Redact a PyMuPDF page that has no text layer (scanned).
    Renders the page to a PIL Image, applies OCR redaction, then replaces
    the page contents with the redacted image.
    """
    # Render page at 300 DPI for good OCR accuracy
    mat = fitz.Matrix(_OCR_DPI / 72, _OCR_DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    redacted = _redact_pil_image(
        img, level, custom_entities, redaction_color, ocr_language, entities_counter,
        strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
        min_confidence=min_confidence,
    )

    # Convert back to fitz Pixmap and replace page content
    img_bytes = io.BytesIO()
    redacted.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    # Clear existing page content and insert redacted image
    page.clean_contents()
    page.insert_image(page.rect, stream=img_bytes.read())


def detect_image_page(
    page: fitz.Page,
    page_num: int,
    level: str,
    custom_entities: Optional[list],
    ocr_language: str,
    strategy: str = "presidio",
    llm_base_url: str = None,
    llm_model: str = None,
    llm_api_key: str = None,
    min_confidence: float = 0.0,
) -> List[dict]:
    """
    Detect PII regions on a scanned PDF page (no text layer) without applying redaction.
    Coordinates are normalised by the rendered image dimensions so they map correctly
    back to PDF point-space when denormalised by page.rect.width / page.rect.height.
    """
    mat = fitz.Matrix(_OCR_DPI / 72, _OCR_DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return _detect_pil_image(
        img, page_num, level, custom_entities, ocr_language,
        strategy=strategy, llm_base_url=llm_base_url, llm_model=llm_model, llm_api_key=llm_api_key,
        min_confidence=min_confidence,
    )


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
