"""
Guardrails Module

Provides safety patterns for LLM interactions:
- Input validation (block prompt injection, inappropriate content)
- Output filtering (remove PII, harmful content)
- Content moderation layer
"""

from .code_review import CodeReview, CodeReviewComment, review_code
from .config import GuardrailsConfig
from .indirect import (
    IndirectFinding,
    IndirectFindingKind,
    IndirectInjectionScanner,
    IndirectScanResult,
    scan_external_content,
)
from .input_guard import InputClassification, InputGuard, InputValidationResult
from .moderation import ModerationVerdict, OpenAIModerator, get_moderator
from .output_guard import OutputFilterResult, OutputGuard

__all__ = [
    "CodeReview",
    "CodeReviewComment",
    "GuardrailsConfig",
    "IndirectFinding",
    "IndirectFindingKind",
    "IndirectInjectionScanner",
    "IndirectScanResult",
    "InputClassification",
    "InputGuard",
    "InputValidationResult",
    "ModerationVerdict",
    "OpenAIModerator",
    "OutputFilterResult",
    "OutputGuard",
    "get_moderator",
    "review_code",
    "scan_external_content",
]
