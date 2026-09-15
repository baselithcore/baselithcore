"""An LLM span must name the plugin the call was made on behalf of.

The plugin context is bound for every request routed to a plugin and around
every orchestrator dispatch, but the GenAI span did not carry it. Without that
key a reader can see that *something* spent tokens and not see who, which is
the difference between a per-plugin view that covers the whole deployment and
one that covers only the work the orchestrator happens to mediate.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.context import reset_plugin_context, set_plugin_context
from core.observability.agent_spans import PLUGIN_KEY
from core.services.llm._generation import _build_span_attributes, _current_plugin


class _Config:
    provider = "openai"


class _Service:
    config = _Config()


def attributes() -> dict[str, Any]:
    return _build_span_attributes(
        _Service(),  # type: ignore[arg-type]
        model="gpt-4o-mini",
        prompt="hello",
        json_mode=False,
        temperature=None,
        max_tokens=None,
    )


@pytest.fixture(autouse=True)
def _clean_context() -> Any:
    """No test may leak a bound plugin into the next one."""
    yield
    token = set_plugin_context(None)
    reset_plugin_context(token)


def test_span_names_the_bound_plugin() -> None:
    token = set_plugin_context("aura")
    try:
        assert attributes()[PLUGIN_KEY] == "aura"
    finally:
        reset_plugin_context(token)


def test_key_is_absent_outside_any_plugin() -> None:
    """Core routes, background jobs and scripts are not a plugin's work."""
    assert PLUGIN_KEY not in attributes()


def test_semconv_attributes_are_untouched() -> None:
    """The addition is additive: nothing a GenAI backend reads may move."""
    token = set_plugin_context("wikigen")
    try:
        bag = attributes()
    finally:
        reset_plugin_context(token)

    assert bag["gen_ai.operation.name"] == "chat"
    assert bag["gen_ai.request.model"] == "gpt-4o-mini"
    assert bag["gen_ai.system"]


def test_a_failing_context_lookup_does_not_fail_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attribution is telemetry: it must never break a completion."""
    import core.context

    def explode() -> str | None:
        raise RuntimeError("context backend down")

    monkeypatch.setattr(core.context, "get_current_plugin", explode)

    assert _current_plugin() is None
    assert PLUGIN_KEY not in attributes()
