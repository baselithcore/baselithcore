"""Tests for agent-attributed spans (``core.observability.agent_spans``)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.observability.agent_spans import (
    AGENT_ID_KEY,
    AGENT_KIND_KEY,
    AGENT_NAME_KEY,
    AGENT_PARENT_KEY,
    OPERATION_KEY,
    PLUGIN_KEY,
    TOOL_NAME_KEY,
    agent_id_for,
    agent_span,
    current_agent_id,
    current_agent_name,
    tool_span,
)
from core.observability.span_sink import (
    SpanRecord,
    register_span_sink,
    unregister_span_sink,
)


@pytest.fixture
def spans() -> Any:
    """Collect every span emitted during the test."""
    collected: list[SpanRecord] = []
    register_span_sink(collected.append)
    try:
        yield collected
    finally:
        unregister_span_sink(collected.append)


def by_name(spans: list[SpanRecord], name: str) -> SpanRecord:
    """Return the single span whose name is *name*."""
    matches = [span for span in spans if span.name == name]
    assert len(matches) == 1, f"expected one {name!r} span, got {len(matches)}"
    return matches[0]


class TestAgentIdFor:
    def test_qualifies_with_plugin(self) -> None:
        assert agent_id_for("researcher", "baselithmed") == "baselithmed:researcher"

    def test_core_prefix_when_unowned(self) -> None:
        assert agent_id_for("researcher") == "core:researcher"
        assert agent_id_for("researcher", None) == "core:researcher"

    def test_same_name_different_plugins_stay_distinct(self) -> None:
        assert agent_id_for("critic", "a") != agent_id_for("critic", "b")


class TestAgentSpan:
    def test_emits_semconv_attributes(self, spans: list[SpanRecord]) -> None:
        with agent_span("qa_docs", plugin="auth"):
            pass

        span = by_name(spans, "invoke_agent qa_docs")
        assert span.attributes[OPERATION_KEY] == "invoke_agent"
        assert span.attributes[AGENT_NAME_KEY] == "qa_docs"
        assert span.attributes[AGENT_ID_KEY] == "auth:qa_docs"
        assert span.attributes[AGENT_KIND_KEY] == "handler"
        assert span.attributes[PLUGIN_KEY] == "auth"

    def test_plugin_key_absent_when_unowned(self, spans: list[SpanRecord]) -> None:
        with agent_span("core_intent"):
            pass

        assert PLUGIN_KEY not in by_name(spans, "invoke_agent core_intent").attributes

    def test_root_agent_has_no_parent(self, spans: list[SpanRecord]) -> None:
        with agent_span("root"):
            pass

        assert AGENT_PARENT_KEY not in by_name(spans, "invoke_agent root").attributes

    def test_nested_agent_records_parent(self, spans: list[SpanRecord]) -> None:
        with agent_span("parent", plugin="auth"), agent_span("child", kind="swarm"):
            pass

        child = by_name(spans, "invoke_agent child")
        assert child.attributes[AGENT_PARENT_KEY] == "auth:parent"
        assert child.attributes[AGENT_KIND_KEY] == "swarm"

    def test_explicit_agent_id_wins(self, spans: list[SpanRecord]) -> None:
        with agent_span("display", plugin="auth", agent_id="custom:id"):
            pass

        assert (
            by_name(spans, "invoke_agent display").attributes[AGENT_ID_KEY]
            == "custom:id"
        )

    def test_extra_attributes_merge(self, spans: list[SpanRecord]) -> None:
        with agent_span("a", attributes={"custom.key": 7}):
            pass

        assert by_name(spans, "invoke_agent a").attributes["custom.key"] == 7

    def test_context_is_set_during_and_cleared_after(self) -> None:
        assert current_agent_id() is None
        with agent_span("qa_docs", plugin="auth"):
            assert current_agent_id() == "auth:qa_docs"
            assert current_agent_name() == "qa_docs"
        assert current_agent_id() is None
        assert current_agent_name() is None

    def test_context_restored_after_nesting(self) -> None:
        with agent_span("outer", plugin="p"):
            with agent_span("inner"):
                assert current_agent_id() == "core:inner"
            assert current_agent_id() == "p:outer"

    def test_exception_propagates_and_marks_span_failed(
        self, spans: list[SpanRecord]
    ) -> None:
        with pytest.raises(ValueError, match="boom"):
            with agent_span("failing"):
                raise ValueError("boom")

        assert by_name(spans, "invoke_agent failing").status == "error"
        assert current_agent_id() is None

    def test_context_cleared_even_when_tracing_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tracer that refuses to start must not leak the agent context."""

        def explode(_name: str) -> Any:
            raise RuntimeError("tracer down")

        monkeypatch.setattr("core.observability.agent_spans.get_tracer", explode)

        with agent_span("resilient") as span:
            assert span is None
            assert current_agent_id() == "core:resilient"
        assert current_agent_id() is None


class TestToolSpan:
    def test_attributes_and_attribution(self, spans: list[SpanRecord]) -> None:
        with agent_span("researcher", plugin="auth"), tool_span("web_search"):
            pass

        span = by_name(spans, "execute_tool web_search")
        assert span.attributes[OPERATION_KEY] == "execute_tool"
        assert span.attributes[TOOL_NAME_KEY] == "web_search"
        assert span.attributes[AGENT_ID_KEY] == "auth:researcher"
        assert span.attributes[AGENT_PARENT_KEY] == "auth:researcher"
        assert span.attributes[AGENT_KIND_KEY] == "tool"

    def test_does_not_become_the_current_agent(self) -> None:
        """A tool is a leaf: a later sibling agent descends from the agent."""
        with agent_span("researcher", plugin="auth"):
            with tool_span("web_search"):
                assert current_agent_id() == "auth:researcher"
            assert current_agent_id() == "auth:researcher"

    def test_unattributed_outside_any_agent(self, spans: list[SpanRecord]) -> None:
        with tool_span("orphan_tool"):
            pass

        span = by_name(spans, "execute_tool orphan_tool")
        assert span.attributes[AGENT_ID_KEY] == "core:unattributed"


class TestAsyncIsolation:
    async def test_concurrent_agents_do_not_leak_into_each_other(self) -> None:
        """Sibling tasks each see their own agent, never the other's."""
        observed: dict[str, str | None] = {}

        async def run(name: str, delay: float) -> None:
            with agent_span(name, plugin="p"):
                await asyncio.sleep(delay)
                observed[name] = current_agent_id()

        await asyncio.gather(run("first", 0.02), run("second", 0.01))

        assert observed == {"first": "p:first", "second": "p:second"}
        assert current_agent_id() is None
