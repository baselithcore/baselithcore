"""
Guardrails Module

Provides safety patterns for LLM interactions:
- Input validation (block prompt injection, inappropriate content)
- Output filtering (remove PII, harmful content)
- Content moderation layer
"""

from .input_guard import InputGuard, InputValidationResult
from .output_guard import OutputGuard, OutputFilterResult
from .config import GuardrailsConfig
from .indirect import (
    IndirectFinding,
    IndirectFindingKind,
    IndirectInjectionScanner,
    IndirectScanResult,
    scan_external_content,
)

__all__ = [
    "InputGuard",
    "InputValidationResult",
    "OutputGuard",
    "OutputFilterResult",
    "GuardrailsConfig",
    "IndirectInjectionScanner",
    "IndirectScanResult",
    "IndirectFinding",
    "IndirectFindingKind",
    "scan_external_content",
]
