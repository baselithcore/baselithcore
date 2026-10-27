"""TEMPLATE, ASSIGN and ITERATION nodes, run through the real executor."""

import asyncio
import itertools

import pytest

from baselith.core.workflows.builder import (
    NodeType,
    WorkflowBuilder,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from baselith.core.workflows.executor import ExecutionStatus, WorkflowExecutor

pytestmark = [pytest.mark.unit]


def _chain(*nodes: WorkflowNode) -> WorkflowDefinition:
    wf = WorkflowDefinition(name="t")
    start = WorkflowNode(id="start", type=NodeType.START, label="s")
    end = WorkflowNode(id="end", type=NodeType.END, label="e")
    ordered = [start, *nodes, end]
    for node in ordered:
        wf.add_node(node)
    for i, (a, b) in enumerate(itertools.pairwise(ordered)):
        wf.add_edge(WorkflowEdge(id=f"e{i}", source_id=a.id, target_id=b.id))
    return wf


def _node(node_id: str, node_type: NodeType, **config) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, label=node_id, config=config)


async def test_template_node_renders_input():
    wf = _chain(_node("t", NodeType.TEMPLATE, template="Hello {{ input }}!"))
    result = await WorkflowExecutor().execute(wf, initial_input="Ada")
    assert result.status is ExecutionStatus.COMPLETED
    assert result.output == "Hello Ada!"


async def test_template_node_with_missing_reference_fails_the_run():
    wf = _chain(_node("t", NodeType.TEMPLATE, template="{{ nodes.ghost }}"))
    result = await WorkflowExecutor().execute(wf, initial_input="x")
    assert result.status is ExecutionStatus.FAILED
    assert "unresolved" in (result.error or "")


async def test_assign_sets_variables_and_passes_output_through():
    wf = _chain(
        _node("a", NodeType.ASSIGN, assign={"who": "{{ input }}", "n": 2}),
        _node("t", NodeType.TEMPLATE, template="{{ who }}-{{ n }}"),
    )
    result = await WorkflowExecutor().execute(wf, initial_input="ada")
    assert result.output == "ada-2"


async def test_assign_coalesce_takes_first_resolvable_non_empty_path():
    wf = _chain(
        _node("first", NodeType.TEMPLATE, template="{{ input }}"),
        _node(
            "pick",
            NodeType.ASSIGN,
            coalesce={"into": "answer", "paths": ["nodes.nope", "nodes.first"]},
        ),
        _node("t", NodeType.TEMPLATE, template="{{ answer }}"),
    )
    result = await WorkflowExecutor().execute(wf, initial_input="v")
    assert result.output == "v"


async def test_assign_coalesce_with_nothing_resolvable_fails():
    wf = _chain(
        _node("pick", NodeType.ASSIGN, coalesce={"into": "a", "paths": ["nodes.x"]})
    )
    result = await WorkflowExecutor().execute(wf, initial_input="v")
    assert result.status is ExecutionStatus.FAILED


@pytest.mark.parametrize("name", ["input", "output", "nodes", "bad name", "1x"])
async def test_assign_rejects_reserved_and_invalid_names(name):
    wf = _chain(_node("a", NodeType.ASSIGN, assign={name: 1}))
    result = await WorkflowExecutor().execute(wf, initial_input="v")
    assert result.status is ExecutionStatus.FAILED


def _doubler() -> dict:
    sub = (
        WorkflowBuilder("double")
        .start()
        .transform("dbl", transform=lambda x: x * 2)
        .end()
        .build()
    )
    return sub.to_dict() | {"nodes": [n.to_dict() | {"config": {}} for n in sub.nodes]}


def _sub_with_tool() -> WorkflowDefinition:
    wf = WorkflowBuilder("sub").start().tool("t", tool_id="double").end().build()
    return wf


async def test_iteration_maps_items_in_order():
    executor = WorkflowExecutor(tools={"double": lambda x: x * 2})
    wf = _chain(
        _node("it", NodeType.ITERATION, workflow=_sub_with_tool(), items="{{ input }}")
    )
    result = await executor.execute(wf, initial_input=[1, 2, 3])
    assert result.output == [2, 4, 6]


async def test_iteration_accepts_serialized_nested_workflow():
    executor = WorkflowExecutor(tools={"double": lambda x: x * 2})
    nested = _sub_with_tool().to_dict()
    wf = _chain(_node("it", NodeType.ITERATION, workflow=nested, items="{{ input }}"))
    result = await executor.execute(wf, initial_input=[5])
    assert result.output == [10]


async def test_iteration_fails_fast_by_default():
    def boom(x):
        if x == 2:
            raise ValueError("bad item")
        return x

    wf = _chain(
        _node("it", NodeType.ITERATION, workflow=_sub_with_tool(), items="{{ input }}")
    )
    result = await WorkflowExecutor(tools={"double": boom}).execute(
        wf, initial_input=[1, 2, 3]
    )
    assert result.status is ExecutionStatus.FAILED
    assert "item 1 failed" in (result.error or "")


async def test_iteration_on_error_continue_records_the_error():
    def boom(x):
        if x == 2:
            raise ValueError("bad item")
        return x * 10

    wf = _chain(
        _node(
            "it",
            NodeType.ITERATION,
            workflow=_sub_with_tool(),
            items="{{ input }}",
            on_error="continue",
        )
    )
    result = await WorkflowExecutor(tools={"double": boom}).execute(
        wf, initial_input=[1, 2, 3]
    )
    assert result.status is ExecutionStatus.COMPLETED
    assert result.output[0] == 10 and result.output[2] == 30
    assert "item 1 failed" in result.output[1]["error"]


async def test_iteration_concurrency_runs_items_together():
    running = 0
    peak = 0

    async def slow(x):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return x

    wf = _chain(
        _node(
            "it",
            NodeType.ITERATION,
            workflow=_sub_with_tool(),
            items="{{ input }}",
            concurrency=4,
        )
    )
    result = await WorkflowExecutor(tools={"double": slow}).execute(
        wf, initial_input=list(range(8))
    )
    assert result.output == list(range(8))
    assert 1 < peak <= 4


async def test_iteration_cancels_siblings_when_an_item_fails():
    started = []
    finished = []

    async def work(x):
        started.append(x)
        if x == 0:
            raise ValueError("first fails")
        await asyncio.sleep(0.3)
        finished.append(x)
        return x

    wf = _chain(
        _node(
            "it",
            NodeType.ITERATION,
            workflow=_sub_with_tool(),
            items="{{ input }}",
            concurrency=4,
        )
    )
    result = await WorkflowExecutor(tools={"double": work}).execute(
        wf, initial_input=[0, 1, 2]
    )
    assert result.status is ExecutionStatus.FAILED
    assert finished == []


@pytest.mark.parametrize(
    ("config", "needle"),
    [
        ({"items": "{{ input }}", "max_items": 2}, "exceed max_items"),
        ({"items": "{{ input }}", "on_error": "ignore"}, "on_error"),
    ],
)
async def test_iteration_guards(config, needle):
    wf = _chain(_node("it", NodeType.ITERATION, workflow=_sub_with_tool(), **config))
    result = await WorkflowExecutor(tools={"double": lambda x: x}).execute(
        wf, initial_input=[1, 2, 3]
    )
    assert result.status is ExecutionStatus.FAILED
    assert needle in (result.error or "")


async def test_iteration_requires_a_list():
    wf = _chain(
        _node("it", NodeType.ITERATION, workflow=_sub_with_tool(), items="{{ input }}")
    )
    result = await WorkflowExecutor().execute(wf, initial_input="not a list")
    assert result.status is ExecutionStatus.FAILED
    assert "list" in (result.error or "")


async def test_tool_node_arguments_are_templated_keywords():
    seen = {}

    def tool(*, who, times):
        seen.update(who=who, times=times)
        return f"{who}x{times}"

    wf = _chain(
        _node(
            "t",
            NodeType.TOOL,
            arguments={"who": "{{ input }}", "times": 2},
        )
    )
    wf.nodes[1].tool_id = "greet"
    result = await WorkflowExecutor(tools={"greet": tool}).execute(
        wf, initial_input="ada"
    )
    assert result.output == "adax2"
    assert seen == {"who": "ada", "times": 2}
