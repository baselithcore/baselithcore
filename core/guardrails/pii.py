"""Optional NER-based PII redaction engine (Presidio).

The built-in redaction is regex-based (see ``config.PII_PATTERNS``) — fast,
dependency-free, but blind to context-dependent PII (names, addresses,
locations). This seam lets deployments swap in Microsoft Presidio:

* install the extra: ``pip install baselith-core[pii]``;
* select it: ``BASELITH_PII_ENGINE=presidio``.

The regex pass remains the always-on fallback: engine unavailable, not
installed, or failing at runtime → the guard silently redacts with regexes
instead, never with nothing.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger

logger = get_logger(__name__)

_ENV = "BASELITH_PII_ENGINE"


@runtime_checkable
class PIIEngine(Protocol):
    """Redaction contract: text in, (redacted text, type→count) out."""

    def redact(self, text: str) -> tuple[str, dict[str, int]]: ...


class PresidioEngine:
    """Presidio-backed NER redaction (built lazily; heavy models load once)."""

    def __init__(self) -> None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine

        self._analyzer = AnalyzerEngine()
        self._anonymizer = AnonymizerEngine()

    def redact(self, text: str) -> tuple[str, dict[str, int]]:
        results = self._analyzer.analyze(text=text, language="en")
        if not results:
            return text, {}
        anonymized: Any = self._anonymizer.anonymize(
            text=text, analyzer_results=results
        )
        counts: dict[str, int] = {}
        for finding in results:
            key = str(finding.entity_type).lower()
            counts[key] = counts.get(key, 0) + 1
        return anonymized.text, counts


@lru_cache(maxsize=1)
def get_pii_engine() -> Any | None:
    """Resolve the configured PII engine, or ``None`` for the regex default."""
    name = os.environ.get(_ENV, "").strip().lower()
    if not name:
        return None
    if name != "presidio":
        logger.warning("pii_engine_unknown", extra={"engine": name})
        return None
    try:
        return PresidioEngine()
    except ImportError:
        logger.warning(
            "pii_engine_presidio_not_installed: pip install baselith-core[pii]"
        )
        return None
    except Exception as exc:  # model bootstrap failures
        logger.warning("pii_engine_presidio_init_failed", extra={"error": str(exc)})
        return None


__all__ = ["PIIEngine", "PresidioEngine", "get_pii_engine"]
