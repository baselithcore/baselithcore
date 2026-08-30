"""First-class graph registration on the Orchestrator (unification phase 1).

``register_handler`` + a hand-built ``WorkflowFlowHandler`` worked, but the
graph path deserves the same ergonomics as the imperative one:
``orchestrator.register_workflow(intent, workflow)`` is the recommended way
to route an intent into a graph. LOOP nodes fail closed instead of silently
passing traffic through (the HUMAN-node lesson, applied to the last
handler-less node type).
"""

from __future__ import annotations

from typing import Any

import pytest

from core.orchestration.orchestrator import Orchestrator
from core.workflows.builder import WorkflowBuilder
from core.workflows.executor import ExecutionStatus, WorkflowExecutor


def _workflow():
    return (
        WorkflowBuilder("graph-intent")
        .start()
        .tool("shout", tool_id="shout")
        .end()
        .build()
    )


def _executor() -> WorkflowExecutor:
    async def shout(value: Any) -> str:
        return f"{value}!".upper()

    return WorkflowExecutor(tools={"shout": shout})


@pytest.mark.asyncio
async def test_register_workflow_routes_intent_into_the_graph(monkeypatch):
    orch = Orchestrator()
    orch.register_workflow("graph_intent", _workflow(), executor=_executor())

    async def fake_classify(query: str) -> str:
        return "graph_intent"

    monkeypatch.setattr(orch, "classify_intent_async", fake_classify)
    result = await orch.process("hello")

    assert result["response"] == "HELLO!"
    assert result["metadata"]["workflow"] == "graph-intent"


def test_register_workflow_appears_among_registered_intents():
    orch = Orchestrator()
    orch.register_workflow("graph_intent", _workflow(), executor=_executor())
    assert "graph_intent" in orch.get_registered_intents()


@pytest.mark.asyncio
async def test_loop_node_fails_closed_not_pass_through():
    workflow = (
        WorkflowBuilder("looped")
        .start()
        ._add_node(  # no builder sugar for LOOP on purpose — unsupported
            __import__("core.workflows.builder", fromlist=["NodeType"]).NodeType.LOOP,
            "loop",
        )
        .end()
        .build()
    )
    result = await WorkflowExecutor().execute(workflow, initial_input="x")

    assert result.status == ExecutionStatus.FAILED
    assert "LOOP" in (result.error or "")
    assert "CONDITION" in (result.error or "")  # the error teaches the fix
