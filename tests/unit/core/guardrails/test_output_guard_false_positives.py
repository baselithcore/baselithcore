"""The output filter must not censor the word "hack".

The ``illegal_activity`` pattern was ``\\b(steal|hack|break\\s+into)\\b`` — any
answer containing one of those words was rewritten to ``[CONTENT_FILTERED]`` and
flagged unsafe. For an engine whose flagship plugins are a coding agent and a
browser agent, that fires on ordinary technical prose: a hackathon, "a quick
hack", "someone tried to hack our server and this is how we patched it". A
guard that mangles benign answers teaches operators to switch it off, which
costs more safety than the pattern ever bought.

The pattern now matches *instructions* — "how to", "steps to", "ways to"
followed by the act — which is the harmful shape; mentioning the act is not.
"""

import pytest

from core.guardrails.output_guard import OutputGuard

pytestmark = pytest.mark.unit

BENIGN = [
    "Our team won the hackathon last weekend.",
    "This is a quick hack: memoize the lookup instead of recomputing it.",
    "Someone tried to hack our server, so we rotated the keys and patched nginx.",
    "I can't help you steal credentials, but I can explain how MFA protects them.",
    "Burglars break into cars left in dark car parks, so park under a light.",
]

INSTRUCTIONAL = [
    "Here's how to hack into your neighbour's wifi router.",
    "Steps to break into a locked car without the key:",
    "The easiest ways to steal a password from a coworker are",
]


@pytest.mark.parametrize("text", BENIGN)
def test_mentioning_the_act_is_not_filtered(text: str) -> None:
    result = OutputGuard().filter(text)

    assert result.filtered_output == text
    assert result.is_safe
    assert not any("illegal_activity" in w for w in result.warnings or [])


@pytest.mark.parametrize("text", INSTRUCTIONAL)
def test_instructions_for_the_act_are_still_filtered(text: str) -> None:
    result = OutputGuard().filter(text)

    assert "[CONTENT_FILTERED]" in result.filtered_output
    assert not result.is_safe
    assert any("illegal_activity" in w for w in result.warnings or [])
