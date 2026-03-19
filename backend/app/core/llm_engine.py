"""
LLM-based PII detection using an OpenAI-compatible API (Ollama, vLLM, etc.).
Returns span objects compatible with Presidio RecognizerResult interface.
"""
import json
import logging
from typing import List

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 3000


class _LLMSpan:
    """Minimal span object with the same attributes used from RecognizerResult."""
    __slots__ = ("start", "end", "entity_type", "score")

    def __init__(self, start: int, end: int, entity_type: str, score: float = 0.85):
        self.start = start
        self.end = end
        self.entity_type = entity_type
        self.score = score


def _build_prompt(text: str, entity_types: List[str]) -> str:
    types_str = ", ".join(entity_types)
    return (
        "You are a PII detection assistant. Find all instances of the following "
        "entity types in the text below and return them as a JSON array.\n\n"
        f"Entity types to find: {types_str}\n\n"
        "Rules:\n"
        '- Return ONLY a JSON array: [{"entity_type": "PERSON", "text": "John Smith"}, ...]\n'
        '- "text" must be the EXACT verbatim substring as it appears in the source text\n'
        "- Include all occurrences\n"
        "- If nothing is found, return []\n\n"
        "Text:\n"
        f"{text}"
    )


def _parse_response(response_text: str) -> List[dict]:
    """Extract JSON array from LLM response, handling markdown fences."""
    text = response_text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```") and not in_block:
                in_block = True
                continue
            if line.startswith("```") and in_block:
                break
            if in_block:
                inner.append(line)
        text = "\n".join(inner).strip()

    # Find first '[' to handle any leading text
    bracket = text.find("[")
    if bracket != -1:
        text = text[bracket:]

    return json.loads(text)


def _find_all_occurrences(source: str, substring: str, offset: int) -> List[tuple]:
    """Return (start, end) for every occurrence of substring in source, adjusted by offset."""
    positions = []
    start = 0
    while True:
        idx = source.find(substring, start)
        if idx == -1:
            break
        positions.append((offset + idx, offset + idx + len(substring)))
        start = idx + 1
    return positions


def _chunk_text(text: str) -> List[tuple]:
    """
    Split text on double-newline boundaries into chunks of at most _CHUNK_SIZE chars.
    Returns list of (chunk_text, cumulative_offset).
    """
    paragraphs = text.split("\n\n")
    chunks = []
    current = []
    current_len = 0
    offset = 0
    chunk_start = 0

    for para in paragraphs:
        # +2 for the "\n\n" separator (except first)
        sep_len = 2 if current else 0
        para_len = len(para)

        if current_len + sep_len + para_len > _CHUNK_SIZE and current:
            chunk_text = "\n\n".join(current)
            chunks.append((chunk_text, chunk_start))
            # advance offset past the joined chunk + the separator we're about to skip
            chunk_start = offset + len(chunk_text) + 2
            offset = chunk_start
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += sep_len + para_len

    if current:
        chunks.append(("\n\n".join(current), chunk_start))

    return chunks


def analyze_text_llm(
    text: str,
    entity_types: List[str],
    base_url: str,
    model: str,
    api_key: str = "ollama",
) -> List[_LLMSpan]:
    """
    Run LLM-based PII detection on text.

    Returns a list of _LLMSpan objects (with .start, .end, .entity_type, .score).
    On any error (network, timeout, bad JSON), logs a warning and returns [].
    """
    if not text or not text.strip() or not entity_types:
        return []

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed; skipping LLM detection")
        return []

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
    except Exception as e:
        logger.warning(f"LLM client init failed: {e}")
        return []

    spans: List[_LLMSpan] = []
    chunks = _chunk_text(text)

    for chunk_text, chunk_offset in chunks:
        prompt = _build_prompt(chunk_text, entity_types)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                timeout=60,
            )
            raw = response.choices[0].message.content or "[]"
        except Exception as e:
            logger.warning(f"LLM API call failed: {e}; skipping chunk")
            continue

        try:
            entities = _parse_response(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"LLM response JSON parse error: {e}; raw={raw[:200]!r}")
            continue

        if not isinstance(entities, list):
            logger.warning(f"LLM response is not a list; skipping chunk")
            continue

        for item in entities:
            if not isinstance(item, dict):
                continue
            entity_type = item.get("entity_type", "")
            match_text = item.get("text", "")
            if not entity_type or not match_text:
                continue

            # Find every occurrence of this string in the chunk
            occurrences = _find_all_occurrences(chunk_text, match_text, chunk_offset)
            for start, end in occurrences:
                spans.append(_LLMSpan(start=start, end=end, entity_type=entity_type))

    return spans
