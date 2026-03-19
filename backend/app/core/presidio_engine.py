"""
Singleton wrapper around Presidio AnalyzerEngine.
Loaded once at startup to avoid repeated spaCy model loading.
"""
import logging
from typing import List, Optional
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from app.core.redaction_levels import get_entities_for_level

logger = logging.getLogger(__name__)

_analyzer: Optional[AnalyzerEngine] = None


def get_analyzer() -> AnalyzerEngine:
    global _analyzer
    if _analyzer is None:
        logger.info("Initializing Presidio AnalyzerEngine with en_core_web_lg...")
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
        })
        nlp_engine = provider.create_engine()
        registry = RecognizerRegistry()
        registry.load_predefined_recognizers(nlp_engine=nlp_engine)
        _analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine,
            registry=registry,
            supported_languages=["en"],
        )
        logger.info("Presidio AnalyzerEngine ready.")
    return _analyzer


def analyze_text(
    text: str,
    level: str,
    custom_entities: list = None,
    language: str = "en",
    strategy: str = "presidio",
    llm_base_url: str = None,
    llm_model: str = None,
    llm_api_key: str = "ollama",
) -> list:
    """
    Returns list of span objects with start, end, entity_type, score.

    strategy="presidio" (default): Presidio only.
    strategy="llm": LLM only.
    strategy="both": Presidio + LLM merged; LLM spans that overlap >80% with a
                     Presidio span are dropped (Presidio is more precise on structured data).
    """
    if not text or not text.strip():
        return []

    entities = get_entities_for_level(level, custom_entities)

    presidio_results = []
    if strategy in ("presidio", "both"):
        analyzer = get_analyzer()
        supported = set(analyzer.get_supported_entities(language=language))
        active_entities = [e for e in entities if e in supported]
        if active_entities:
            try:
                presidio_results = analyzer.analyze(
                    text=text,
                    entities=active_entities,
                    language=language,
                    score_threshold=0.4,
                )
            except Exception as e:
                logger.error(f"Presidio analysis error: {e}")

    if strategy == "presidio":
        return presidio_results

    # LLM path
    from app.core.llm_engine import analyze_text_llm
    llm_results = analyze_text_llm(
        text=text,
        entity_types=entities,
        base_url=llm_base_url or "http://ollama:11434/v1",
        model=llm_model or "llama3.2:3b",
        api_key=llm_api_key or "ollama",
    )

    if strategy == "llm":
        return llm_results

    # strategy == "both": merge, dropping LLM spans that substantially overlap Presidio
    def _overlap_ratio(a_start, a_end, b_start, b_end) -> float:
        overlap = max(0, min(a_end, b_end) - max(a_start, b_start))
        a_len = a_end - a_start
        return overlap / a_len if a_len else 0.0

    merged = list(presidio_results)
    for llm_span in llm_results:
        dominated = any(
            _overlap_ratio(llm_span.start, llm_span.end, p.start, p.end) > 0.8
            for p in presidio_results
        )
        if not dominated:
            merged.append(llm_span)

    return merged


def get_supported_entities(language: str = "en") -> List[str]:
    return sorted(get_analyzer().get_supported_entities(language=language))
