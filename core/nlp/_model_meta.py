"""Model introspection for embedding telemetry: name and token count.

Split out of :mod:`core.nlp.models` for the module size cap; ``models``
re-exports both helpers under their old names.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


def _model_name(model: Any) -> str:
    """Best-effort model identifier for span/metric labels."""
    card = getattr(model, "model_card_data", None)
    for candidate in (
        getattr(card, "base_model", None),
        getattr(card, "model_name", None),
        getattr(model, "model_name", None),
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return type(model).__name__


def _count_tokens_sync(model: Any, texts: list[str]) -> int | None:
    """Blocking token count for *texts*, or ``None`` if unavailable."""
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None or not texts:
        return None
    try:
        encoded = tokenizer(texts)
        ids = encoded["input_ids"] if isinstance(encoded, dict) else encoded.input_ids
        return sum(len(row) for row in ids)
    except Exception as exc:  # telemetry must never break an embedding
        # `exc` is a tokenizer failure, not a credential; the rule matches on
        # "token" (NLP tokenization here, not an auth token).
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.debug("[embedder] token count unavailable: %s", exc)
        return None


__all__ = ["_count_tokens_sync", "_model_name"]
