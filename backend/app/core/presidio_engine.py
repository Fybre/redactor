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


def analyze_text(text: str, level: str, custom_entities: list = None, language: str = "en") -> list:
    """
    Returns list of RecognizerResult with start, end, entity_type, score.
    """
    if not text or not text.strip():
        return []

    analyzer = get_analyzer()
    entities = get_entities_for_level(level, custom_entities)

    # Filter to entities the analyzer actually supports
    supported = set(analyzer.get_supported_entities(language=language))
    active_entities = [e for e in entities if e in supported]

    if not active_entities:
        return []

    try:
        results = analyzer.analyze(
            text=text,
            entities=active_entities,
            language=language,
            score_threshold=0.4,
        )
        return results
    except Exception as e:
        logger.error(f"Presidio analysis error: {e}")
        return []


def get_supported_entities(language: str = "en") -> List[str]:
    return sorted(get_analyzer().get_supported_entities(language=language))
