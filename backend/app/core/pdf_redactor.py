"""
Redacts text-layer PDFs using PyMuPDF.

For each page:
1. Extract words with bounding boxes.
2. Reconstruct full page text, building a char-offset → word-bbox mapping.
3. Run Presidio on the full text.
4. For each PII span, collect the affected word bboxes.
5. Apply PyMuPDF redact annotations (removes text from content stream) AND
   draw filled black rectangles (visible redaction boxes).
6. Save with garbage collection to strip orphaned objects.
"""
import logging
from collections import defaultdict
from typing import List, Tuple, Dict, Any
import fitz  # PyMuPDF

from app.core.presidio_engine import analyze_text

logger = logging.getLogger(__name__)


def _build_char_to_word_map(words: list) -> List[Tuple[int, int, fitz.Rect]]:
    """
    Given words from page.get_text("words"), build a list of
    (char_start, char_end, rect) tuples for the reconstructed text.

    Words are (x0, y0, x1, y1, text, block_no, line_no, word_no).
    We reconstruct the text by joining words with spaces, tracking offsets.
    """
    mapping = []  # (char_start, char_end, rect)
    offset = 0
    for w in words:
        x0, y0, x1, y1, word_text = w[0], w[1], w[2], w[3], w[4]
        rect = fitz.Rect(x0, y0, x1, y1)
        char_end = offset + len(word_text)
        mapping.append((offset, char_end, rect))
        offset = char_end + 1  # +1 for the space separator
    return mapping


def _reconstruct_text(words: list) -> str:
    return " ".join(w[4] for w in words)


def _rects_for_span(char_start: int, char_end: int, mapping: List[Tuple[int, int, fitz.Rect]]) -> List[fitz.Rect]:
    """Return all word rects that overlap with the character span [char_start, char_end)."""
    rects = []
    for w_start, w_end, rect in mapping:
        # Word overlaps with span if ranges intersect
        if w_start < char_end and w_end > char_start:
            rects.append(rect)
    return rects


def _merge_rects(rects: List[fitz.Rect]) -> fitz.Rect:
    """Return the bounding box that encompasses all rects."""
    if not rects:
        return fitz.Rect()
    merged = rects[0]
    for r in rects[1:]:
        merged = merged | r
    # Add small padding
    return merged + (-1, -1, 1, 1)


def redact_pdf(
    input_path: str,
    output_path: str,
    level: str,
    custom_entities: list = None,
    redaction_color: tuple = (0, 0, 0),
    ocr_language: str = "eng",
) -> Dict[str, Any]:
    """
    Redact a text-layer PDF. Returns stats dict with page_count and entities_found.
    """
    from app.core.image_redactor import redact_image_page

    doc = fitz.open(input_path)
    total_entities: Dict[str, int] = defaultdict(int)
    page_count = doc.page_count

    color_normalized = tuple(c / 255.0 for c in redaction_color)

    for page_num, page in enumerate(doc):
        # Check if page has a meaningful text layer
        text_blocks = page.get_text("words")
        full_text = _reconstruct_text(text_blocks)

        if len(full_text.strip()) < 10:
            # Scanned page — use OCR-based redaction inline
            logger.debug(f"Page {page_num}: no text layer, using OCR redaction")
            redact_image_page(page, level, custom_entities, redaction_color, ocr_language, total_entities)
            continue

        char_map = _build_char_to_word_map(text_blocks)
        pii_results = analyze_text(full_text, level, custom_entities)

        for result in pii_results:
            total_entities[result.entity_type] += 1
            rects = _rects_for_span(result.start, result.end, char_map)
            if not rects:
                continue
            merged = _merge_rects(rects)

            # Step 1: Add redact annotation — PyMuPDF will remove text from content stream
            page.add_redact_annot(merged, fill=color_normalized)

        # Apply redactions: removes text from the PDF content stream
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    # Save with garbage=4 to remove all unreferenced objects
    doc.save(output_path, garbage=4, deflate=True)
    doc.close()

    return {
        "page_count": page_count,
        "entities_found": dict(total_entities),
    }
