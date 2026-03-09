"""
Advanced Linguistic Analysis via spaCy.

Provides high-performance NLP utilities for deep text inspection.
Implements lazy-loading for spaCy pipelines, named entity recognition (NER),
token/sentence breakdown, and automated metadata extraction for
enriching the knowledge graph.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from functools import lru_cache
from typing import Any, Dict, Optional, Type

from core.config import get_processing_config

logger = get_logger(__name__)

_spacy_module: Optional[Any] = None
_LanguageType: Optional[Type[Any]] = None

try:  # pragma: no cover - import guard for environments without spaCy
    import spacy as _spacy_internal
    from spacy.language import Language as _LanguageInternal

    _spacy_module = _spacy_internal
    _LanguageType = _LanguageInternal
except ModuleNotFoundError:  # pragma: no cover - handled at runtime
    pass


@lru_cache(maxsize=1)
def get_spacy_pipeline() -> Optional[Any]:
    """Returns (and optionally loads) the configured spaCy pipeline."""
    proc_config = get_processing_config()

    if not proc_config.enable_spacy_documents:
        return None

    if _spacy_module is None:
        logger.warning(
            "spaCy library is not available. Install the package to enable NLP features."
        )
        return None

    model_name = proc_config.spacy_model

    try:
        nlp = _spacy_module.load(model_name)
    except OSError as exc:
        fallback_language = proc_config.spacy_fallback_language or (
            model_name.split("_", 1)[0] if model_name else "en"
        )
        logger.warning(
            f"Model '{model_name}' not available ({exc}). Attempting fallback with blank pipeline '{fallback_language}'."
        )
        try:
            nlp = _spacy_module.blank(fallback_language)
            if not nlp.has_pipe("sentencizer"):
                nlp.add_pipe("sentencizer")
        except Exception as blank_exc:  # pragma: no cover - rare fallback
            logger.error(
                f"Unable to create spaCy fallback pipeline '{fallback_language}': {blank_exc}"
            )
            return None
    except Exception as exc:  # pragma: no cover - unexpected errors
        logger.error(f"Unexpected error loading spaCy model '{model_name}': {exc}")
        return None
    else:
        if not (
            nlp.has_pipe("parser")
            or nlp.has_pipe("senter")
            or nlp.has_pipe("sentencizer")
        ):
            try:
                nlp.add_pipe("sentencizer")
            except Exception as exc:  # pragma: no cover - very rare
                logger.warning(
                    f"Unable to add sentencizer to the spaCy pipeline: {exc}"
                )

    return nlp


def is_spacy_available() -> bool:
    """Allows quick verification of whether spaCy is usable."""

    return get_spacy_pipeline() is not None


def extract_spacy_metadata(
    text: str,
    *,
    max_entities: int = 8,
) -> Dict[str, str]:
    """Derives auxiliary textual metadata using spaCy."""

    nlp = get_spacy_pipeline()
    if nlp is None:
        return {}

    try:
        doc = nlp(text)
    except Exception as exc:  # pragma: no cover - avoid crash in fallback
        logger.warning(f"spaCy processing failed: {exc}")
        return {}

    metadata: Dict[str, str] = {}

    language = getattr(nlp, "lang", None) or "unknown"
    metadata["spacy_language"] = language

    proc_config = get_processing_config()
    model_name = nlp.meta.get("name") if hasattr(nlp, "meta") else None
    metadata["spacy_model"] = model_name or proc_config.spacy_model or "unknown"

    token_count = sum(1 for token in doc if not token.is_space)
    metadata["spacy_token_count"] = str(token_count)

    sentence_count = sum(1 for _ in doc.sents)
    metadata["spacy_sentence_count"] = str(sentence_count)

    unique_entities = []
    seen = set()
    for ent in doc.ents:
        text_value = " ".join(ent.text.strip().split())
        if not text_value:
            continue
        label = ent.label_.strip() if ent.label_ else ""
        descriptor = f"{text_value} ({label})" if label else text_value
        dedupe_key = descriptor.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        unique_entities.append(descriptor)
        if len(unique_entities) >= max_entities:
            break

    if unique_entities:
        metadata["spacy_entities"] = "; ".join(unique_entities)

    return metadata
