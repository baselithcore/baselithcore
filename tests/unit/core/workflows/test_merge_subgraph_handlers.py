"""Tests for MERGE fan-in, SUBGRAPH composition, and default AGENT/TOOL handlers."""

import pytest

from core.workflows.builder import (
    NodeType,
    WorkflowBuilder,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from core.workflows.executor import ExecutionStatus, WorkflowExecutor

pytestmark = [pytest.mark.unit]


def _parallel_merge_workflow():
    """start → parallel → (branch a, branch b) → merge → transform → end."""
    wf = WorkflowDefinition(name="fanin")
    nodes = [
        WorkflowNode(id="start", type=NodeType.START, label="Start"),
        WorkflowNode(id="par", type=NodeType.PARALLEL, label="Par"),
        WorkflowNode(
            id="a",
            type=NodeType.TRANSFORM,
            label="A",
            config={"transform": lambda _x: "out-a"},
        ),
        WorkflowNode(
            id="b",
            type=NodeType.TRANSFORM,
            label="B",
            config={"transform": lambda _x: "out-b"},
        ),
        WorkflowNode(id="merge", type=NodeType.MERGE, label="Merge"),
        WorkflowNode(
            id="post",
            type=NodeType.TRANSFORM,
            label="Post",
            config={"transform": lambda merged: sorted(merged)},
        ),
        WorkflowNode(id="end", type=NodeType.END, label="End"),
    ]
    for n in nodes:
        wf.add_node(n)
    edges = [
        ("start", "par"),
        ("par", "a"),
        ("par", "b"),
        ("a", "merge"),
        ("b", "merge"),
        ("merge", "post"),
        ("post", "end"),
    ]
    for i, (s, t) in enumerate(edges):
        wf.add_edge(WorkflowEdge(id=f"e{i}", source_id=s, target_id=t))
    return wf


@pytest.mark.asyncio
class TestMergeFanIn:
    async def test_merge_executes_and_downstream_runs(self):
        executor = WorkflowExecutor()
        result = await executor.execute(_parallel_merge_workflow(), "in")
        assert result.status == ExecutionStatus.COMPLETED
        assert "merge" in result.node_results  # the old bug: merge never ran
        assert sorted(result.node_results["merge"].output) == ["out-a", "out-b"]
        assert result.node_results["post"].output == ["out-a", "out-b"]
        assert result.output == ["out-a", "out-b"]

    async def test_divergent_merges_fail_clearly(self):
        wf = _parallel_merge_workflow()
        # Point branch b at a second, different merge node.
        wf.add_node(WorkflowNode(id="merge2", type=NodeType.MERGE, label="M2"))
        wf.add_edge(WorkflowEdge(id="ex", source_id="merge2", target_id="end"))
        for edge in wf.edges:
            if edge.source_id == "b" and edge.target_id == "merge":
                edge.target_id = "merge2"
        executor = WorkflowExecutor()
        result = await executor.execute(wf, "in")
        assert result.status == ExecutionStatus.FAILED
        assert "merge" in (result.error or "").lower()


@pytest.mark.asyncio
class TestSubgraph:
    def _inner(self):
        return (
            WorkflowBuilder("inner")
            .start()
            .transform("double", transform=lambda x: f"{x}{x}")
            .end()
            .build()
        )

    async def test_subgraph_output_propagates(self):
        outer = (
            WorkflowBuilder("outer")
            .start()
            .subgraph("nested", workflow=self._inner())
            .end()
            .build()
        )
        result = await WorkflowExecutor().execute(outer, "ab")
        assert result.status == ExecutionStatus.COMPLETED
        assert result.output == "abab"

    async def test_subgraph_accepts_serialized_dict(self):
        # NOTE: callables don't survive serialization; use a pass-through inner.
        inner = WorkflowBuilder("inner").start().transform("noop").end().build()
        outer = (
            WorkflowBuilder("outer")
            .start()
            .subgraph("nested", workflow=inner.to_dict())
            .end()
            .build()
        )
        result = await WorkflowExecutor().execute(outer, "payload")
        assert result.status == ExecutionStatus.COMPLETED
        assert result.output == "payload"

    async def test_subgraph_failure_fails_parent(self):
        def _boom(_x):
            raise RuntimeError("inner exploded")

        inner = (
            WorkflowBuilder("inner")
            .start()
            .transform("boom", transform=_boom)
            .end()
            .build()
        )
        outer = (
            WorkflowBuilder("outer")
            .start()
            .subgraph("nested", workflow=inner)
            .end()
            .build()
        )
        result = await WorkflowExecutor().execute(outer, "x")
        assert result.status == ExecutionStatus.FAILED
        assert "inner exploded" in (result.error or "")

    async def test_missing_subgraph_config_fails(self):
        outer = WorkflowBuilder("outer").start().subgraph("nested").end().build()
        result = await WorkflowExecutor().execute(outer, "x")
        assert result.status == ExecutionStatus.FAILED


class _StubAgent:
    def __init__(self, reply="agent-reply"):
        self.reply = reply
        self.prompts = []

    async def run(self, prompt):
        self.prompts.append(prompt)

        class _R:
            output = self.reply
            text = self.reply

        return _R()


@pytest.mark.asyncio
class TestAgentToolHandlers:
    async def test_agent_node_runs_registry_agent(self):
        stub = _StubAgent("hi from agent")
        wf = (
            WorkflowBuilder("agents")
            .start()
            .agent("step", agent_id="writer")
            .end()
            .build()
        )
        executor = WorkflowExecutor(agents={"writer": stub})
        result = await executor.execute(wf, "the input")
        assert result.status == ExecutionStatus.COMPLETED
        assert result.output == "hi from agent"
        assert "the input" in stub.prompts[0]

    async def test_agent_node_inline_config_instance(self):
        stub = _StubAgent("inline")
        wf = (
            WorkflowBuilder("agents")
            .start()
            .agent("step", agent_id="ignored", agent=stub)
            .end()
            .build()
        )
        result = await WorkflowExecutor().execute(wf, "x")
        assert result.status == ExecutionStatus.COMPLETED
        assert result.output == "inline"

    async def test_unresolvable_agent_fails(self):
        wf = (
            WorkflowBuilder("agents")
            .start()
            .agent("step", agent_id="ghost")
            .end()
            .build()
        )
        result = await WorkflowExecutor().execute(wf, "x")
        assert result.status == ExecutionStatus.FAILED
        assert "ghost" in (result.error or "")

    async def test_tool_node_calls_registry_fn(self):
        async def shout(text):
            return str(text).upper()

        wf = (
            WorkflowBuilder("tools").start().tool("loud", tool_id="shout").end().build()
        )
        executor = WorkflowExecutor(tools={"shout": shout})
        result = await executor.execute(wf, "quiet")
        assert result.status == ExecutionStatus.COMPLETED
        assert result.output == "QUIET"

    async def test_tool_node_inline_fn(self):
        wf = (
            WorkflowBuilder("tools")
            .start()
            .tool("rev", tool_id="ignored", fn=lambda s: s[::-1])
            .end()
            .build()
        )
        result = await WorkflowExecutor().execute(wf, "abc")
        assert result.status == ExecutionStatus.COMPLETED
        assert result.output == "cba"
