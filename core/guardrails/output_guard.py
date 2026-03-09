"""
Output Guard - Post-LLM Output Filtering

Filters LLM output before returning to user:
- PII detection and redaction
- Harmful content filtering
- Output length validation
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.observability.logging import get_logger

from .config import (
    GuardrailsConfig,
    COMPILED_PII_PATTERNS,
)

logger = get_logger(__name__)


@dataclass
class OutputFilterResult:
    """Result of output filtering."""

    is_safe: bool
    filtered_output: str
    redactions: Optional[Dict[str, int]] = None  # type -> count
    warnings: Optional[List[str]] = None


# Harmful content patterns (simplified)
HARMFUL_PATTERNS = [
    (r"\b(kill|murder|harm|hurt)\s+(yourself|someone|people)\b", "violence"),
    (r"\b(how\s+to\s+make|build|create)\s+(a\s+)?(bomb|weapon|explosive)\b", "weapons"),
    (r"\b(steal|hack|break\s+into)\b", "illegal_activity"),
]


class OutputGuard:
    """
    Filters LLM output before returning to user.

    Filters:
    - PII (emails, phones, SSN, credit cards)
    - Harmful content patterns
    - Excessive output length
    """

    def __init__(self, config: Optional[GuardrailsConfig] = None):
        """
        Initialize OutputGuard.

        Args:
            config: Guardrails configuration (uses defaults if None)
        """
        self.config = config or GuardrailsConfig()
        self._harmful_patterns = [
            (re.compile(p, re.IGNORECASE), category) for p, category in HARMFUL_PATTERNS
        ]

    def filter(self, text: str) -> OutputFilterResult:
        """
        Filter output text.

        Args:
            text: Output text to filter

        Returns:
            OutputFilterResult with filtered output and metadata
        """
        if not self.config.output_enabled:
            return OutputFilterResult(is_safe=True, filtered_output=text)

        filtered = text
        redactions: Dict[str, int] = {}
        warnings: List[str] = []

        # Truncate if too long
        if len(filtered) > self.config.max_output_length:
            filtered = filtered[: self.config.max_output_length]
            warnings.append(
                f"Output truncated to {self.config.max_output_length} chars"
            )

        # Filter PII
        if self.config.filter_pii:
            filtered, pii_redactions = self._redact_pii(filtered)
            redactions.update(pii_redactions)

        # Filter harmful content
        if self.config.filter_harmful_content:
            filtered, harmful_detected = self._filter_harmful(filtered)
            if harmful_detected:
                warnings.extend(harmful_detected)

        is_safe = len(warnings) == 0 or all("truncated" in w for w in warnings)

        if redactions:
            logger.info(f"Redacted PII from output: {redactions}")

        return OutputFilterResult(
            is_safe=is_safe,
            filtered_output=filtered,
            redactions=redactions if redactions else None,
            warnings=warnings if warnings else None,
        )

    def _redact_pii(self, text: str) -> tuple[str, Dict[str, int]]:
        """
        Redact PII from text.

        Args:
            text: Text to redact

        Returns:
            Tuple of (redacted text, redaction counts by type)
        """
        result = text
        counts: Dict[str, int] = {}

        for pii_type, pattern in COMPILED_PII_PATTERNS.items():
            matches = pattern.findall(result)
            if matches:
                counts[pii_type] = len(matches)
                result = pattern.sub(f"[{pii_type.upper()}_REDACTED]", result)

        return result, counts

    def _filter_harmful(self, text: str) -> tuple[str, List[str]]:
        """
        Filter harmful content.

        Args:
            text: Text to filter

        Returns:
            Tuple of (filtered text, list of detected categories)
        """
        result = text
        detected = []

        for pattern, category in self._harmful_patterns:
            if pattern.search(result):
                result = pattern.sub("[CONTENT_FILTERED]", result)
                detected.append(f"harmful_content:{category}")

        return result, detected

    def check_safety(self, text: str) -> bool:
        """
        Quick check if text is safe without filtering.

        Args:
            text: Text to check

        Returns:
            True if text appears safe
        """
        # Check for harmful patterns
        for pattern, _ in self._harmful_patterns:
            if pattern.search(text):
                return False

        return True
