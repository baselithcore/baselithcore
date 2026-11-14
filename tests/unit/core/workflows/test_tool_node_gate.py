"""Workflow TOOL nodes run through the single tool-enforcement chokepoint.

Regression: ``handle_tool`` called the registered function directly. The Agent
loop, the ReAct loop and the orchestrator handlers all push a tool call through
``enforce_tool_invocation`` (agent contract, plugin capability, autonomy
approval, tool-call budget, rate limit, pre-hooks, audit trail); a workflow
TOOL node bypassed every one of them, so the same tool was gated in a chat turn
and free in a workflow.

The gate reads the orchestration context the request already carries, handed to
``WorkflowExecutor.execute(gate_context=...)`` (the flow handler passes its own
``context``). Without one the executor behaves exactly as before.
"""

from __future__ import annotations

from typing import Any

from baselith.core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy
from baselith.core.orchestration.contract import (
    AgentContract,
    Capabilities,
    ContractValidator,
)
from baselith.core.orchestration.limits import LoopBudget, LoopLimits
from baselith.core.workflows.builder import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from baselith.core.workflows.executor import (
    ExecutionStatus,
    NodeType,
    WorkflowExecutor,
)
from baselith.core.workflows.flow_handler import WorkflowFlowHandler


def _chain(*tool_nodes: WorkflowNode, wf_id: str = "gate-wf") -> WorkflowDefinition:
    """START -> tool nodes (in order) -> END."""
    wf = WorkflowDefinition(id=wf_id, name=wf_id)
    wf.add_node(WorkflowNode(id="start", type=NodeType.START, label="Start"))
    previous = "start"
    for node in tool_nodes:
        wf.add_node(node)
        wf.add_edge(
            WorkflowEdge(
                id=f"{previous}->{node.id}", source_id=previous, target_id=node.id
            )
        )
        previous = node.id
    wf.add_node(WorkflowNode(id="end", type=NodeType.END, label="End"))
    wf.add_edge(
        WorkflowEdge(id=f"{previous}->end", source_id=previous, target_id="end")
    )
    return wf


def _tool(node_id: str, tool_id: str, **config: Any) -> WorkflowNode:
    return WorkflowNode(
        id=node_id, type=NodeType.TOOL, label=node_id, tool_id=tool_id, config=config
    )


def _forbidding(*tool_names: str) -> ContractValidator:
    return ContractValidator(
        AgentContract(
            name="wf-agent",
            version="1.0",
            identity="test",
            capabilities=Capabilities(must_not=list(tool_names)),
        )
    )


def _recording_tools(*names: str) -> tuple[list[str], dict[str, Any]]:
    calls: list[str] = []

    def make(name: str) -> Any:
        def fn(_last_output: Any = None) -> str:
            calls.append(name)
            return f"{name}-ok"

        return fn

    return calls, {name: make(name) for name in names}


async def test_tool_runs_unchanged_without_a_gate_context() -> None:
    calls, tools = _recording_tools("wipe")
    executor = WorkflowExecutor(tools=tools)

    result = await executor.execute(_chain(_tool("t", "wipe")))

    assert result.status == ExecutionStatus.COMPLETED
    assert calls == ["wipe"]


async def test_contract_forbidden_tool_is_refused_and_never_called() -> None:
    calls, tools = _recording_tools("wipe")
    executor = WorkflowExecutor(tools=tools)

    result = await executor.execute(
        _chain(_tool("t", "wipe")),
        gate_context={"contract_validator": _forbidding("wipe")},
    )

    assert result.status == ExecutionStatus.FAILED
    assert "explicitly forbidden" in (result.error or "")
    assert calls == []


async def test_tool_calls_are_charged_to_the_loop_budget() -> None:
    calls, tools = _recording_tools("a", "b")
    executor = WorkflowExecutor(tools=tools)
    budget = LoopBudget(limits=LoopLimits(max_tool_calls=1))

    result = await executor.execute(
        _chain(_tool("first", "a"), _tool("second", "b")),
        gate_context={"loop_budget": budget},
    )

    assert result.status == ExecutionStatus.FAILED
    assert calls == ["a"]
    assert budget.tool_calls >= 1


async def test_undeclared_tool_is_gated_as_destructive() -> None:
    calls, tools = _recording_tools("send")
    executor = WorkflowExecutor(tools=tools)
    gate = {"autonomy_policy": AutonomyPolicy(level=AutonomyLevel.SUPERVISED)}

    result = await executor.execute(_chain(_tool("t", "send")), gate_context=gate)

    # No category declared and no human-approval channel on the context: the
    # same fail-closed outcome the Agent and ReAct loops give an undeclared tool.
    assert result.status == ExecutionStatus.FAILED
    assert calls == []


async def test_declared_read_only_tool_passes_the_approval_gate() -> None:
    calls, tools = _recording_tools("lookup")
    executor = WorkflowExecutor(tools=tools)
    gate = {"autonomy_policy": AutonomyPolicy(level=AutonomyLevel.SUPERVISED)}

    result = await executor.execute(
        _chain(_tool("t", "lookup", category="read_only")), gate_context=gate
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert calls == ["lookup"]


async def test_unknown_declared_category_is_treated_as_destructive() -> None:
    calls, tools = _recording_tools("send")
    executor = WorkflowExecutor(tools=tools)
    gate = {"autonomy_policy": AutonomyPolicy(level=AutonomyLevel.SUPERVISED)}

    result = await executor.execute(
        _chain(_tool("t", "send", category="harmless-ish")), gate_context=gate
    )

    assert result.status == ExecutionStatus.FAILED
    assert calls == []


async def test_flow_handler_hands_the_orchestration_context_to_the_gate() -> None:
    calls, tools = _recording_tools("wipe")
    handler = WorkflowFlowHandler(
        _chain(_tool("t", "wipe")), executor=WorkflowExecutor(tools=tools)
    )

    response = await handler.handle("go", {"contract_validator": _forbidding("wipe")})

    assert response["error"] is True
    assert calls == []


async def test_subgraph_inherits_the_gate() -> None:
    calls, tools = _recording_tools("wipe")
    inner = _chain(_tool("t", "wipe"), wf_id="inner")
    outer = _chain(
        WorkflowNode(
            id="sub", type=NodeType.SUBGRAPH, label="sub", config={"workflow": inner}
        ),
        wf_id="outer",
    )

    result = await WorkflowExecutor(tools=tools).execute(
        outer, gate_context={"contract_validator": _forbidding("wipe")}
    )

    # A tool inside a nested graph is no less a tool call than one at the top.
    assert result.status == ExecutionStatus.FAILED
    assert calls == []


async def test_iteration_inherits_the_gate() -> None:
    calls, tools = _recording_tools("wipe")
    inner = _chain(_tool("t", "wipe"), wf_id="inner")
    outer = _chain(
        WorkflowNode(
            id="each",
            type=NodeType.ITERATION,
            label="each",
            config={"workflow": inner},
        ),
        wf_id="outer",
    )

    result = await WorkflowExecutor(tools=tools).execute(
        outer,
        initial_input=["a", "b"],
        gate_context={"contract_validator": _forbidding("wipe")},
    )

    assert result.status == ExecutionStatus.FAILED
    assert calls == []
