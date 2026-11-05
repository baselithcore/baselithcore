"""LIST (declarative list reshaping) and CODE (sandboxed snippet) nodes."""

from types import SimpleNamespace

import pytest

from baselith.core.config.workflows import WorkflowsConfig
from baselith.core.workflows.builder import (
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from baselith.core.workflows.executor import ExecutionStatus, WorkflowExecutor

pytestmark = [pytest.mark.unit]

HITS = [
    {"id": "a", "score": 0.9, "tags": ["x", "y"], "doc": "d1"},
    {"id": "b", "score": 0.4, "tags": ["z"], "doc": "d2"},
    {"id": "c", "score": 0.7, "tags": [], "doc": "d1"},
    {"id": "d", "score": 0.8, "tags": ["x"], "doc": "d3"},
]


def _wf(node: WorkflowNode) -> WorkflowDefinition:
    wf = WorkflowDefinition(name="t")
    for n in (
        WorkflowNode(id="start", type=NodeType.START, label="s"),
        node,
        WorkflowNode(id="end", type=NodeType.END, label="e"),
    ):
        wf.add_node(n)
    wf.add_edge(WorkflowEdge(id="1", source_id="start", target_id=node.id))
    wf.add_edge(WorkflowEdge(id="2", source_id=node.id, target_id="end"))
    return wf


def _list_node(*ops, **config) -> WorkflowNode:
    return WorkflowNode(
        id="l", type=NodeType.LIST, label="l", config={"ops": list(ops), **config}
    )


async def _run(node, value=HITS, **services):
    return await WorkflowExecutor(services=services).execute(_wf(node), value)


class TestList:
    async def test_the_documented_pipeline(self):
        node = _list_node(
            {"filter": "item['score'] > 0.5"},
            {"sort": {"key": "score", "descending": True}},
            {"unique": "doc"},
            {"limit": 2},
            {"pluck": "id"},
            items="{{ input }}",
        )
        result = await _run(node)
        # kept a, c, d; sorted a(0.9) d(0.8) c(0.7); unique doc keeps a, d, drops c.
        assert result.output == ["a", "d"]

    async def test_defaults_to_the_previous_output(self):
        assert (await _run(_list_node({"limit": 1}))).output == [HITS[0]]

    async def test_sort_shorthand_and_scalars(self):
        node = _list_node({"sort": None}, {"reverse": True}, items="{{ input }}")
        assert (await _run(node, [3, 1, 2])).output == [3, 2, 1]
        assert (await _run(_list_node({"sort": "score"}), HITS)).output[0]["id"] == "b"

    async def test_filter_sees_item_and_index(self):
        node = _list_node({"filter": "index < 2"}, {"pluck": "id"}, items="{{ input }}")
        assert (await _run(node)).output == ["a", "b"]

    async def test_offset_limit_unique_flatten(self):
        assert (
            await _run(_list_node({"offset": 1}, {"limit": 2}, {"pluck": "id"}))
        ).output == ["b", "c"]
        assert (
            await _run(_list_node({"unique": True}), [1, 1, 2, {"a": 1}, {"a": 1}])
        ).output == [1, 2, {"a": 1}]
        node = _list_node({"pluck": "tags"}, {"flatten": True})
        assert (await _run(node)).output == ["x", "y", "z", "x"]

    @pytest.mark.parametrize(
        ("ops", "needle"),
        [
            ([{"filter": "item['nope'] > 1"}], "filter failed on item 0"),
            ([{"sort": "nope"}], "no 'nope' in an item"),
            ([{"pluck": "nope"}], "no 'nope' in an item"),
            ([{"limit": -1}], "non-negative integer"),
            ([{"limit": True}], "non-negative integer"),
            ([{"filter": "__import__('os')"}], "filter failed"),
            ([{"frobnicate": 1}], "unknown step"),
            ([{"limit": 1, "offset": 1}], "one-key mapping"),
        ],
    )
    async def test_bad_steps_fail_the_node(self, ops, needle):
        result = await _run(_list_node(*ops))
        assert result.status is ExecutionStatus.FAILED
        assert needle in (result.error or "")

    async def test_mixed_types_cannot_be_sorted(self):
        result = await _run(_list_node({"sort": None}, items="{{ input }}"), [1, "a"])
        assert (
            result.status is ExecutionStatus.FAILED and "not comparable" in result.error
        )

    async def test_input_must_be_a_list_and_bounded(self, monkeypatch):
        assert (
            await _run(_list_node({"limit": 1}), "text")
        ).status is ExecutionStatus.FAILED
        monkeypatch.setattr("baselith.core.workflows.nodes_list.MAX_LIST_ITEMS", 3)
        assert (
            await _run(_list_node({"limit": 1}), HITS)
        ).status is ExecutionStatus.FAILED

    def test_validation_catches_unknown_and_missing_steps_before_a_run(self):
        assert (
            WorkflowDefinition.from_dict(
                _wf(_list_node({"limit": 1})).to_dict()
            ).validate()
            == []
        )
        bad = _wf(_list_node({"explode": 1})).validate()
        assert any("unknown step 'explode'" in e for e in bad)
        assert any("needs a non-empty" in e for e in _wf(_list_node()).validate())
        assert any(
            "one-key mapping" in e for e in _wf(_list_node({"a": 1, "b": 2})).validate()
        )


def _code_node(**config) -> WorkflowNode:
    return WorkflowNode(id="c", type=NodeType.CODE, label="c", config=config)


class Runner:
    """Stands in for the sandbox and records what it was asked to run."""

    def __init__(self, stdout="", stderr="", exit_code=0):
        self.result = SimpleNamespace(stdout=stdout, stderr=stderr, exit_code=exit_code)
        self.calls = []

    async def __call__(self, code, *, envs, timeout):
        self.calls.append({"code": code, "envs": envs, "timeout": timeout})
        return self.result


@pytest.fixture
def code_on(monkeypatch):
    monkeypatch.setattr(
        "baselith.core.config.workflows.get_workflows_config",
        lambda: WorkflowsConfig(WORKFLOWS_CODE_ENABLED="true"),
    )


class TestCode:
    async def test_disabled_by_default_and_never_reaches_the_sandbox(self):
        runner = Runner('{"a": 1}')
        result = await _run(_code_node(code="print(1)"), "x", code_runner=runner)
        assert result.status is ExecutionStatus.FAILED
        assert "WORKFLOWS_CODE_ENABLED" in result.error
        assert runner.calls == []

    async def test_inputs_travel_as_json_and_json_stdout_is_parsed(self, code_on):
        runner = Runner('{"total": 6}\n')
        node = _code_node(
            code="print(1)",
            inputs={"numbers": "{{ input }}", "label": "n={{ input | length }}"},
            timeout=5,
        )
        result = await _run(node, [1, 2, 3], code_runner=runner)
        assert result.output == {"total": 6}
        call = runner.calls[0]
        assert call["code"] == "print(1)" and call["timeout"] == 5
        assert call["envs"] == {
            "WORKFLOW_INPUT": '{"numbers": [1, 2, 3], "label": "n=3"}'
        }

    async def test_default_inputs_and_text_stdout(self, code_on):
        runner = Runner("plain words\n")
        result = await _run(
            _code_node(code="print('plain words')"), "hello", code_runner=runner
        )
        assert result.output == "plain words"
        assert runner.calls[0]["envs"] == {"WORKFLOW_INPUT": '{"input": "hello"}'}
        assert runner.calls[0]["timeout"] is None

    async def test_non_zero_exit_fails_with_the_stderr_tail(self, code_on):
        runner = Runner(
            stderr="x" * 1000 + "Traceback...ZeroDivisionError", exit_code=1
        )
        result = await _run(_code_node(code="1/0"), "x", code_runner=runner)
        assert result.status is ExecutionStatus.FAILED
        assert "exited with 1" in result.error and "ZeroDivisionError" in result.error
        assert len(result.error) < 700

    @pytest.mark.parametrize(
        ("config", "needle"),
        [
            ({}, "needs a config['code']"),
            ({"code": "  "}, "needs a config['code']"),
            ({"code": "1", "language": "bash"}, "only python"),
            ({"code": "1", "inputs": "{{ input }}"}, "inputs must be a mapping"),
            ({"code": "1", "inputs": {"blob": "{{ input }}"}}, "larger than"),
        ],
    )
    async def test_bad_configuration(self, code_on, config, needle):
        runner = Runner("1")
        value = "y" * 70_000
        result = await _run(
            _code_node(**config),
            value if "blob" in str(config) else ["a"],
            code_runner=runner,
        )
        assert result.status is ExecutionStatus.FAILED and needle in result.error
        assert runner.calls == []

    async def test_default_runner_uses_the_sandbox_service(self, code_on, monkeypatch):
        seen = {}

        class FakeSandbox:
            async def execute_code_async(
                self, code, language, timeout=None, envs=None, **kw
            ):
                seen.update(code=code, language=language, timeout=timeout, envs=envs)
                return SimpleNamespace(stdout="42", stderr="", exit_code=0)

        monkeypatch.setattr(
            "baselith.core.services.sandbox.service.SandboxService", FakeSandbox
        )
        result = await WorkflowExecutor().execute(
            _wf(_code_node(code="print(42)", timeout=7)), "q"
        )
        assert result.output == 42
        assert seen["language"] == "python" and seen["timeout"] == 7
        assert seen["envs"]["WORKFLOW_INPUT"] == '{"input": "q"}'

    def test_validation(self):
        assert _wf(_code_node(code="print(1)")).validate() == []
        assert any("needs a config['code']" in e for e in _wf(_code_node()).validate())
        assert any(
            "only python" in e
            for e in _wf(_code_node(code="1", language="ruby")).validate()
        )
