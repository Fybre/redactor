"""
Singleton wrapper around Presidio AnalyzerEngine.
Loaded once at startup to avoid repeated spaCy model loading.
"""
import logging
from typing import List, Optional
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from app.core.redaction_levels import get_entities_for_level, ENTITY_DESCRIPTIONS

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
        entity_descriptions=ENTITY_DESCRIPTIONS,
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


def _recognizer_type(rec) -> str:
    from presidio_analyzer import PatternRecognizer
    if isinstance(rec, PatternRecognizer):
        if getattr(rec, "deny_list", None):
            return "deny_list"
        return "pattern"
    cls = type(rec).__name__.lower()
    if any(k in cls for k in ("spacy", "transformers", "stanza", "ner")):
        return "ner"
    return "pattern"


def get_entity_info() -> list:
    """Return one entry per entity type with recognizer type and whether it's custom."""
    analyzer = get_analyzer()
    seen = {}
    for rec in analyzer.registry.recognizers:
        rtype = _recognizer_type(rec)
        is_custom = getattr(rec, "_custom", False)
        for entity in rec.supported_entities:
            if entity not in seen:
                seen[entity] = {"recognizer_type": rtype, "custom": is_custom,
                                "recognizer_name": type(rec).__name__}
    return seen


def load_custom_recognizers(recognizers_config: list) -> None:
    """Register custom recognizers from config, replacing any previously loaded ones."""
    from presidio_analyzer import PatternRecognizer, Pattern
    analyzer = get_analyzer()
    # Remove previously loaded custom recognizers
    analyzer.registry.recognizers = [
        r for r in analyzer.registry.recognizers if not getattr(r, "_custom", False)
    ]
    for cfg in recognizers_config:
        try:
            entity_type = cfg["entity_type"]
            name = cfg.get("name", entity_type)
            context = cfg.get("context") or []
            if cfg.get("type") == "pattern":
                patterns = [
                    Pattern(
                        name=p.get("name", "pattern"),
                        regex=p["regex"],
                        score=float(p.get("score", 0.5)),
                    )
                    for p in cfg.get("patterns", []) if p.get("regex")
                ]
                rec = PatternRecognizer(
                    supported_entity=entity_type,
                    name=name,
                    patterns=patterns,
                    context=context,
                )
            elif cfg.get("type") == "deny_list":
                rec = PatternRecognizer(
                    supported_entity=entity_type,
                    name=name,
                    deny_list=cfg.get("deny_list") or [],
                    context=context,
                )
            else:
                continue
            rec._custom = True
            analyzer.registry.add_recognizer(rec)
            logger.info(f"Loaded custom recognizer: {name} ({cfg.get('type')}) → {entity_type}")
        except Exception as e:
            logger.error(f"Failed to load custom recognizer '{cfg.get('name')}': {e}")
