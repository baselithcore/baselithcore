"""
Guardrails Module

Provides safety patterns for LLM interactions:
- Input validation (block prompt injection, inappropriate content)
- Output filtering (remove PII, harmful content)
- Content moderation layer
"""

from .config import GuardrailsConfig
from .indirect import (
    IndirectFinding,
    IndirectFindingKind,
    IndirectInjectionScanner,
    IndirectScanResult,
    scan_external_content,
)
from .input_guard import InputGuard, InputValidationResult
from .moderation import ModerationVerdict, OpenAIModerator, get_moderator
from .output_guard import OutputFilterResult, OutputGuard

__all__ = [
    "GuardrailsConfig",
    "IndirectFinding",
    "IndirectFindingKind",
    "IndirectInjectionScanner",
    "IndirectScanResult",
    "InputGuard",
    "InputValidationResult",
    "ModerationVerdict",
    "OpenAIModerator",
    "OutputFilterResult",
    "OutputGuard",
    "get_moderator",
    "scan_external_content",
]
