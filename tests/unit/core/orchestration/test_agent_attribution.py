"""Tests for dispatch attribution (plugin context + agent span in one scope)."""

from __future__ import annotations

from typing import Any

import pytest

from core.context import get_current_plugin
from core.observability.agent_spans import (
    AGENT_ID_KEY,
    AGENT_KIND_KEY,
    PLUGIN_KEY,
    current_agent_id,
)
from core.observability.span_sink import (
    SpanRecord,
    register_span_sink,
    unregister_span_sink,
)
from core.orchestration.mixins._agent_attribution import (
    dispatch_attribution,
    intent_owner,
)


class _Registry:
    """Minimal stand-in for the plugin registry's owner lookup."""

    def __init__(self, owners: dict[str, str], raises: bool = False) -> None:
        self._owners = owners
        self._raises = raises

    def get_flow_handler_owner(self, intent: str) -> str | None:
        if self._raises:
            raise RuntimeError("registry exploded")
        return self._owners.get(intent)


class _Orchestrator:
    def __init__(self, registry: Any) -> None:
        self.plugin_registry = registry


@pytest.fixture
def spans() -> Any:
    collected: list[SpanRecord] = []
    register_span_sink(collected.append)
    try:
        yield collected
    finally:
        unregister_span_sink(collected.append)


class TestIntentOwner:
    def test_returns_owner(self) -> None:
        orchestrator = _Orchestrator(_Registry({"qa_docs": "auth"}))
        assert intent_owner(orchestrator, "qa_docs") == "auth"

    def test_none_for_unknown_intent(self) -> None:
        orchestrator = _Orchestrator(_Registry({}))
        assert intent_owner(orchestrator, "qa_docs") is None

    def test_none_without_registry(self) -> None:
        assert intent_owner(_Orchestrator(None), "qa_docs") is None

    def test_registry_failure_is_swallowed(self) -> None:
        orchestrator = _Orchestrator(_Registry({}, raises=True))
        assert intent_owner(orchestrator, "qa_docs") is None


class TestDispatchAttribution:
    def test_binds_plugin_and_opens_agent_span(self, spans: list[SpanRecord]) -> None:
        orchestrator = _Orchestrator(_Registry({"qa_docs": "auth"}))

        with dispatch_attribution(orchestrator, "qa_docs") as owner:
            assert owner == "auth"
            assert get_current_plugin() == "auth"
            assert current_agent_id() == "auth:qa_docs"

        assert get_current_plugin() is None
        assert current_agent_id() is None

        span = next(s for s in spans if s.name == "invoke_agent qa_docs")
        assert span.attributes[AGENT_ID_KEY] == "auth:qa_docs"
        assert span.attributes[PLUGIN_KEY] == "auth"
        assert span.attributes[AGENT_KIND_KEY] == "handler"

    def test_core_owned_intent_still_gets_a_span(self, spans: list[SpanRecord]) -> None:
        orchestrator = _Orchestrator(_Registry({}))

        with dispatch_attribution(orchestrator, "complex_reasoning") as owner:
            assert owner is None
            assert current_agent_id() == "core:complex_reasoning"

        span = next(s for s in spans if s.name == "invoke_agent complex_reasoning")
        assert PLUGIN_KEY not in span.attributes

    def test_handler_failure_unbinds_everything(self, spans: list[SpanRecord]) -> None:
        orchestrator = _Orchestrator(_Registry({"qa_docs": "auth"}))

        with pytest.raises(RuntimeError, match="handler blew up"):
            with dispatch_attribution(orchestrator, "qa_docs"):
                raise RuntimeError("handler blew up")

        assert get_current_plugin() is None
        assert current_agent_id() is None
        span = next(s for s in spans if s.name == "invoke_agent qa_docs")
        assert span.status == "error"

    def test_sub_agent_edge_is_recorded(self, spans: list[SpanRecord]) -> None:
        """A swarm sub-agent inside a dispatch records the dispatch as parent."""
        from core.observability.agent_spans import AGENT_PARENT_KEY, agent_span

        orchestrator = _Orchestrator(_Registry({"collaborative_task": "core_swarm"}))

        with dispatch_attribution(orchestrator, "collaborative_task"):
            with agent_span("Researcher", kind="swarm"):
                pass

        child = next(s for s in spans if s.name == "invoke_agent Researcher")
        assert child.attributes[AGENT_PARENT_KEY] == "core_swarm:collaborative_task"
