"""The PII regex layer is linear on adversarial input.

Each case below was quadratic before its pattern was anchored to the start of
its character run: ``"a." * 25000`` alone cost about a second per
``OutputGuard.filter`` call, run synchronously on the event loop.
"""

from __future__ import annotations

import time

import pytest

from core.guardrails.config import COMPILED_PII_PATTERNS
from core.guardrails.output_guard import OutputGuard

ADVERSARIAL = {
    "dotted_run": "a." * 25000,
    "digit_dots": "1." * 25000,
    "at_then_dotted_run": "a@" + "a." * 25000,
    "dashed_jwt_prefix": "-eyJ" * 12500,
    "unterminated_key_headers": "-----BEGIN PRIVATE KEY-----\n" * 1800,
}

BUDGET_SECONDS = 0.2


@pytest.mark.parametrize("text", ADVERSARIAL.values(), ids=ADVERSARIAL.keys())
def test_pii_patterns_are_linear(text: str) -> None:
    start = time.perf_counter()
    for pattern in COMPILED_PII_PATTERNS.values():
        pattern.subn("", text)
    assert time.perf_counter() - start < BUDGET_SECONDS


def test_output_guard_redacts_adversarial_run_quickly() -> None:
    guard = OutputGuard()
    start = time.perf_counter()
    guard.filter("a." * 25000)
    assert time.perf_counter() - start < BUDGET_SECONDS


@pytest.mark.parametrize(
    "email",
    ["john.doe@example.com", "a+tag@sub.example.co.uk", "x_y%z@host-1.io"],
)
def test_ordinary_email_is_still_redacted(email: str) -> None:
    result = OutputGuard().filter(f"write to {email}, thanks")
    assert email not in result.filtered_output
    assert "[EMAIL_REDACTED]" in result.filtered_output
    assert result.redactions and "email" in result.redactions


def test_redaction_count_matches_occurrences() -> None:
    result = OutputGuard().filter("a@b.com and c@d.org")
    assert result.filtered_output == "[EMAIL_REDACTED] and [EMAIL_REDACTED]"
    assert result.redactions == {"email": 2}
