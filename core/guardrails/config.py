"""
Guardrails Configuration

Centralized configuration for safety patterns.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import re


@dataclass
class GuardrailsConfig:
    """Configuration for guardrails system."""

    # Input validation settings
    input_enabled: bool = True
    max_input_length: int = 10000
    block_injection_patterns: bool = True
    block_code_execution: bool = True

    # Output filtering settings
    output_enabled: bool = True
    filter_pii: bool = True
    filter_harmful_content: bool = True
    max_output_length: int = 50000

    # Content moderation
    moderation_enabled: bool = True
    moderation_threshold: float = 0.7

    # Custom patterns to block (regex)
    custom_block_patterns: List[str] = field(default_factory=list)

    # Allowed domains for URLs
    allowed_url_domains: Optional[List[str]] = None


# Default injection patterns to detect
DEFAULT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(all\s+)?(previous|prior|above)",
    r"forget\s+(everything|all|your)\s+(you|instructions?|training)",
    r"you\s+are\s+now\s+(a|an|the)",
    r"pretend\s+(you\s+are|to\s+be)",
    r"act\s+as\s+(if|though|a|an)",
    r"new\s+system\s+prompt",
    r"override\s+(your|the|all)\s+(instructions?|rules?|guidelines?)",
    r"\[system\]",
    r"\[INST\]",
    r"<\|im_start\|>",
    r"<\|system\|>",
]

# Code execution patterns
CODE_EXECUTION_PATTERNS = [
    r"eval\s*\(",
    r"exec\s*\(",
    r"import\s+os",
    r"import\s+subprocess",
    r"__import__",
    r"os\.system",
    r"subprocess\.call",
    r"subprocess\.run",
]

# PII patterns for filtering
PII_PATTERNS = {
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "phone": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
    "ip_address": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
}


def compile_patterns(patterns: List[str]) -> List[re.Pattern]:
    """Compile regex patterns with case insensitivity."""
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            pass  # Skip invalid patterns
    return compiled


# Pre-compiled default patterns
COMPILED_INJECTION_PATTERNS = compile_patterns(DEFAULT_INJECTION_PATTERNS)
COMPILED_CODE_PATTERNS = compile_patterns(CODE_EXECUTION_PATTERNS)
COMPILED_PII_PATTERNS = {
    name: re.compile(pattern) for name, pattern in PII_PATTERNS.items()
}
