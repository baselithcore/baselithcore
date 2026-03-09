import pytest
import asyncio
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta
from core.workflows.executor import (
    WorkflowExecutor,
    ExecutionContext,
    WorkflowResult,
    ExecutionStatus,
    _safe_condition,
    _ast_interpret,
    NodeType,
    NodeResult,
)
from core.workflows.builder import WorkflowDefinition, WorkflowNode, WorkflowEdge


@pytest.fixture
def executor():
    return WorkflowExecutor()


@pytest.fixture
def basic_workflow():
    wf = WorkflowDefinition(id="test-wf", name="Test Workflow")
    start = WorkflowNode(id="start", type=NodeType.START, label="Start")
    end = WorkflowNode(id="end", type=NodeType.END, label="End")
    wf.add_node(start)
    wf.add_node(end)
    wf.add_edge(WorkflowEdge(id="e1", source_id="start", target_id="end"))
    return wf


class TestSafeConditionLineCoverage:
    def test_basic_comparisons(self):
        assert _safe_condition("1 == 1", {}) is True
        assert _safe_condition("1 != 2", {}) is True
        assert _safe_condition("5 > 3", {}) is True
        assert _safe_condition("3 <= 3", {}) is True

    def test_variables(self):
        assert _safe_condition("x > 10", {"x": 15}) is True
        assert _safe_condition("x == 'hello'", {"x": "hello"}) is True

    def test_boolean_ops(self):
        assert _safe_condition("x > 0 and y < 0", {"x": 1, "y": -1}) is True
        assert _safe_condition("x > 0 or y > 0", {"x": 1, "y": -1}) is True
        assert _safe_condition("not x", {"x": False}) is True

    def test_subscript_and_attribute(self):
        class Obj:
            def __init__(self):
                self.val = 42

        assert _safe_condition("a[0] == 1", {"a": [1, 2]}) is True
        assert _safe_condition("obj.val == 42", {"obj": Obj()}) is True

    def test_builtins(self):
        assert _safe_condition("len(s) == 5", {"s": "hello"}) is True
        assert _safe_condition("int('123') == 123", {}) is True

    def test_unsupported(self):
        with pytest.raises(ValueError, match="Function calls not allowed"):
            _safe_condition("eval('1+1')", {})
        with pytest.raises(
            ValueError, match="Access to private/dunder attribute denied"
        ):
            _safe_condition("obj._private", {"obj": MagicMock()})
        with pytest.raises(ValueError, match="Undefined variable"):
            _safe_condition("undef_var", {})

    def test_ast_interpret_extra_coverage(self):
        import ast

        # Line 70: Expression node
        node = ast.parse("1 + 1", mode="eval")
        assert _ast_interpret(node, {}) == 2

        # Line 94: Unsupported comparison
        comp_node = ast.Compare(
            left=ast.Constant(value=1),
            ops=[ast.Not()],
            comparators=[ast.Constant(value=2)],
        )
        with pytest.raises(ValueError, match="Unsupported comparison"):
            _ast_interpret(comp_node, {})

        # Line 104: Unsupported boolean op
        class FakeOp:
            pass

        bool_node = ast.BoolOp(op=FakeOp(), values=[ast.Constant(value=True)])
        with pytest.raises(ValueError, match="Unsupported boolean op"):
            _ast_interpret(bool_node, {})

        # Unary ops
        assert _safe_condition("-x == -5", {"x": 5}) is True

        # BinOp
        assert _safe_condition("x * y == 50", {"x": 10, "y": 5}) is True

        # IfExp (Ternary)
        if_node = ast.parse("'Big' if x > 10 else 'Small'", mode="eval").body
        assert _ast_interpret(if_node, {"x": 20}) == "Big"
        assert _ast_interpret(if_node, {"x": 5}) == "Small"

    def test_ast_interpret_errors(self):
        import ast

        # Line 111: Unsupported unary op
        node = ast.UnaryOp(op=ast.UAdd(), operand=ast.Constant(value=1))
        with pytest.raises(ValueError, match="Unsupported unary op"):
            _ast_interpret(node, {})

        # Line 117: Unsupported binary op
        node = ast.BinOp(
            left=ast.Constant(value=1), op=ast.Pow(), right=ast.Constant(value=2)
        )
        with pytest.raises(ValueError, match="Unsupported binary op"):
            _ast_interpret(node, {})

        # Line 143: Unsupported expression node
        node = ast.Lambda(
            args=ast.arguments(), body=ast.Constant(value=1)
        )  # Just an example node not handled
        with pytest.raises(ValueError, match="Unsupported expression node"):
            _ast_interpret(node, {})


class TestExecutionContext:
    def test_variable_management(self):
        ctx = ExecutionContext(workflow_id="wf1")
        ctx.set_variable("k", "v")
        assert ctx.get_variable("k") == "v"
        assert ctx.get_variable("missing", "default") == "default"

    def test_get_last_output(self):
        ctx = ExecutionContext(workflow_id="wf1")
        assert ctx.get_last_output() is None
        ctx.node_results["n1"] = NodeResult(
            node_id="n1", status=ExecutionStatus.COMPLETED, output="res1"
        )
        assert ctx.get_last_output() == "res1"


class TestWorkflowExecutor:
    @pytest.mark.asyncio
    async def test_execute_basic_workflow(self, executor, basic_workflow):
        result = await executor.execute(basic_workflow, initial_input="hello")
        assert result.status == ExecutionStatus.COMPLETED
        assert result.output == "hello"
        assert len(result.node_results) == 2

    @pytest.mark.asyncio
    async def test_execute_with_transform(self, executor):
        wf = WorkflowDefinition(id="transform-wf", name="Transform WF")
        start = WorkflowNode(id="start", type=NodeType.START, label="Start")
        transform = WorkflowNode(
            id="t1",
            type=NodeType.TRANSFORM,
            label="Transform",
            config={"transform": lambda x: x.upper()},
        )
        end = WorkflowNode(id="end", type=NodeType.END, label="End")
        wf.add_node(start)
        wf.add_node(transform)
        wf.add_node(end)
        wf.add_edge(WorkflowEdge(id="e1", source_id="start", target_id="t1"))
        wf.add_edge(WorkflowEdge(id="e2", source_id="t1", target_id="end"))

        result = await executor.execute(wf, initial_input="hello")
        assert result.status == ExecutionStatus.COMPLETED
        assert result.output == "HELLO"

    @pytest.mark.asyncio
    async def test_execute_with_condition(self, executor):
        wf = WorkflowDefinition(id="cond-wf", name="Condition WF")
        start = WorkflowNode(id="start", type=NodeType.START, label="Start")
        cond = WorkflowNode(
            id="c1",
            type=NodeType.CONDITION,
            label="Cond",
            condition_expression="output > 10",
        )
        big = WorkflowNode(
            id="big",
            type=NodeType.TRANSFORM,
            label="Big",
            config={"transform": lambda x: "BIG"},
        )
        small = WorkflowNode(
            id="small",
            type=NodeType.TRANSFORM,
            label="Small",
            config={"transform": lambda x: "SMALL"},
        )
        end = WorkflowNode(id="end", type=NodeType.END, label="End")
        wf.add_node(start)
        wf.add_node(cond)
        wf.add_node(big)
        wf.add_node(small)
        wf.add_node(end)
        wf.add_edge(WorkflowEdge(id="e1", source_id="start", target_id="c1"))
        wf.add_edge(
            WorkflowEdge(
                id="e2", source_id="c1", target_id="big", condition_label="true"
            )
        )
        wf.add_edge(
            WorkflowEdge(
                id="e3", source_id="c1", target_id="small", condition_label="false"
            )
        )
        wf.add_edge(WorkflowEdge(id="e4", source_id="big", target_id="end"))
        wf.add_edge(WorkflowEdge(id="e5", source_id="small", target_id="end"))

        assert (await executor.execute(wf, initial_input=20)).output == "BIG"
        assert (await executor.execute(wf, initial_input=5)).output == "SMALL"

    @pytest.mark.asyncio
    async def test_execute_parallel(self, executor):
        wf = WorkflowDefinition(id="parallel-wf", name="Parallel WF")
        start = WorkflowNode(id="start", type=NodeType.START, label="Start")
        parallel = WorkflowNode(id="p1", type=NodeType.PARALLEL, label="Parallel")
        branch1 = WorkflowNode(
            id="b1",
            type=NodeType.TRANSFORM,
            label="B1",
            config={"transform": lambda x: x + 1},
        )
        branch2 = WorkflowNode(
            id="b2",
            type=NodeType.TRANSFORM,
            label="B2",
            config={"transform": lambda x: x + 2},
        )
        end = WorkflowNode(id="end", type=NodeType.END, label="End")
        wf.add_node(start)
        wf.add_node(parallel)
        wf.add_node(branch1)
        wf.add_node(branch2)
        wf.add_node(end)
        wf.add_edge(WorkflowEdge(id="e1", source_id="start", target_id="p1"))
        wf.add_edge(WorkflowEdge(id="e2", source_id="p1", target_id="b1"))
        wf.add_edge(WorkflowEdge(id="e3", source_id="p1", target_id="b2"))
        wf.add_edge(WorkflowEdge(id="e4", source_id="b1", target_id="end"))
        wf.add_edge(WorkflowEdge(id="e5", source_id="b2", target_id="end"))

        result = await executor.execute(wf, initial_input=10)
        assert result.status == ExecutionStatus.COMPLETED
        assert "b1" in result.node_results
        assert "b2" in result.node_results

    @pytest.mark.asyncio
    async def test_custom_handler(self, executor):
        executor.register_handler("CUSTOM", lambda n, c: "Custom Result")
        wf = WorkflowDefinition(id="custom-wf")
        wf.add_node(WorkflowNode(id="s", type=NodeType.START, label="S"))
        wf.add_node(WorkflowNode(id="c", type="CUSTOM", label="C"))
        wf.add_node(WorkflowNode(id="e", type=NodeType.END, label="E"))
        wf.add_edge(WorkflowEdge(id="e1", source_id="s", target_id="c"))
        wf.add_edge(WorkflowEdge(id="e2", source_id="c", target_id="e"))
        result = await executor.execute(wf)
        assert result.output == "Custom Result"

    @pytest.mark.asyncio
    async def test_timeout(self, executor):
        async def slow_fn(n, c):
            await asyncio.sleep(0.2)
            return "Done"

        executor.register_handler("SLOW", slow_fn)
        wf = WorkflowDefinition(id="timeout-wf")
        wf.add_node(WorkflowNode(id="s", type=NodeType.START, label="S"))
        wf.add_node(WorkflowNode(id="slow", type="SLOW", label="Slow", timeout=0.05))
        wf.add_node(WorkflowNode(id="e", type=NodeType.END, label="E"))
        wf.add_edge(WorkflowEdge(id="e1", source_id="s", target_id="slow"))
        wf.add_edge(WorkflowEdge(id="e2", source_id="slow", target_id="e"))
        result = await executor.execute(wf)
        assert result.status == ExecutionStatus.FAILED
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_safe_condition_failure_handling(self, executor):
        wf = WorkflowDefinition(id="fail-cond")
        wf.add_node(WorkflowNode(id="s", type=NodeType.START, label="S"))
        wf.add_node(
            WorkflowNode(
                id="c", type=NodeType.CONDITION, label="C", condition_expression="1 + "
            )
        )
        wf.add_node(WorkflowNode(id="e", type=NodeType.END, label="E"))
        wf.add_edge(WorkflowEdge(id="e1", source_id="s", target_id="c"))
        wf.add_edge(WorkflowEdge(id="e2", source_id="c", target_id="e"))
        result = await executor.execute(wf)
        assert result.status == ExecutionStatus.COMPLETED

    def test_workflow_result_duration(self):
        start = datetime.now(timezone.utc)
        res = WorkflowResult(
            workflow_id="wf", status=ExecutionStatus.COMPLETED, started_at=start
        )
        assert res.duration_ms == 0.0
        res.completed_at = start + timedelta(milliseconds=500)
        assert res.duration_ms == 500.0

    @pytest.mark.asyncio
    async def test_execute_empty_condition(self, executor):
        wf = WorkflowDefinition(id="empty-cond")
        wf.add_node(WorkflowNode(id="s", type=NodeType.START, label="S"))
        wf.add_node(
            WorkflowNode(
                id="c", type=NodeType.CONDITION, label="C", condition_expression=""
            )
        )
        wf.add_node(WorkflowNode(id="e", type=NodeType.END, label="E"))
        wf.add_edge(WorkflowEdge(id="e1", source_id="s", target_id="c"))
        wf.add_edge(WorkflowEdge(id="e2", source_id="c", target_id="e"))
        result = await executor.execute(wf)
        assert result.status == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_node_default_transform(self, executor):
        wf = WorkflowDefinition(id="def-trans")
        wf.add_node(WorkflowNode(id="s", type=NodeType.START, label="S"))
        wf.add_node(WorkflowNode(id="t", type=NodeType.TRANSFORM, label="T"))
        wf.add_node(WorkflowNode(id="e", type=NodeType.END, label="E"))
        wf.add_edge(WorkflowEdge(id="e1", source_id="s", target_id="t"))
        wf.add_edge(WorkflowEdge(id="e2", source_id="t", target_id="e"))
        result = await executor.execute(wf, initial_input="test")
        assert result.output == "test"

    @pytest.mark.asyncio
    async def test_validation_failure(self, executor):
        wf = WorkflowDefinition(id="invalid")
        result = await executor.execute(wf)
        assert result.status == ExecutionStatus.FAILED
        assert "Validation failed" in result.error

    @pytest.mark.asyncio
    async def test_no_start_node(self, executor):
        # Manually bypass validation
        wf = WorkflowDefinition(id="no-start")
        wf.nodes = [WorkflowNode(id="e", type=NodeType.END, label="E")]
        from unittest.mock import patch

        with patch(
            "core.workflows.builder.WorkflowDefinition.validate", return_value=[]
        ):
            result = await executor.execute(wf)
            assert result.status == ExecutionStatus.FAILED
            assert "No start node found" in result.error
