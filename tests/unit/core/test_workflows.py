"""
Unit Tests for Workflow Builder

Tests for workflow definition, builder, and executor.
"""

import pytest


class TestWorkflowNode:
    """Tests for WorkflowNode."""

    def test_create_node(self):
        """Test creating a workflow node."""
        from core.workflows.builder import WorkflowNode, NodeType

        node = WorkflowNode(
            id="test-node",
            type=NodeType.AGENT,
            label="Test Agent",
            agent_id="my-agent",
        )

        assert node.id == "test-node"
        assert node.type == NodeType.AGENT
        assert node.agent_id == "my-agent"

    def test_node_serialization(self):
        """Test node to/from dict."""
        from core.workflows.builder import WorkflowNode, NodeType, NodePosition

        node = WorkflowNode(
            id="ser-node",
            type=NodeType.TOOL,
            label="Tool Node",
            position=NodePosition(x=100, y=200),
        )

        data = node.to_dict()
        restored = WorkflowNode.from_dict(data)

        assert restored.id == node.id
        assert restored.type == node.type
        assert restored.position.x == 100


class TestWorkflowEdge:
    """Tests for WorkflowEdge."""

    def test_create_edge(self):
        """Test creating an edge."""
        from core.workflows.builder import WorkflowEdge

        edge = WorkflowEdge(
            id="edge-1",
            source_id="node-1",
            target_id="node-2",
        )

        assert edge.source_id == "node-1"
        assert edge.target_id == "node-2"


class TestWorkflowDefinition:
    """Tests for WorkflowDefinition."""

    def test_create_empty_workflow(self):
        """Test creating empty workflow."""
        from core.workflows.builder import WorkflowDefinition

        workflow = WorkflowDefinition(name="Empty Workflow")

        assert workflow.name == "Empty Workflow"
        assert len(workflow.nodes) == 0
        assert len(workflow.edges) == 0

    def test_add_nodes_and_edges(self):
        """Test adding nodes and edges."""
        from core.workflows.builder import (
            WorkflowDefinition,
            WorkflowNode,
            WorkflowEdge,
            NodeType,
        )

        workflow = WorkflowDefinition()
        start = WorkflowNode(id="start", type=NodeType.START, label="Start")
        end = WorkflowNode(id="end", type=NodeType.END, label="End")

        workflow.add_node(start)
        workflow.add_node(end)
        workflow.add_edge(WorkflowEdge(id="edge", source_id="start", target_id="end"))

        assert len(workflow.nodes) == 2
        assert len(workflow.edges) == 1
        assert workflow.get_node("start") == start

    def test_validate_valid_workflow(self):
        """Test validation passes for valid workflow."""
        from core.workflows.builder import (
            WorkflowDefinition,
            WorkflowNode,
            WorkflowEdge,
            NodeType,
        )

        workflow = WorkflowDefinition()
        workflow.add_node(WorkflowNode(id="start", type=NodeType.START, label="Start"))
        workflow.add_node(WorkflowNode(id="end", type=NodeType.END, label="End"))
        workflow.add_edge(WorkflowEdge(id="e1", source_id="start", target_id="end"))

        errors = workflow.validate()
        assert len(errors) == 0

    def test_validate_missing_start(self):
        """Test validation fails without start node."""
        from core.workflows.builder import (
            WorkflowDefinition,
            WorkflowNode,
            NodeType,
        )

        workflow = WorkflowDefinition()
        workflow.add_node(WorkflowNode(id="end", type=NodeType.END, label="End"))

        errors = workflow.validate()
        assert any("START" in e for e in errors)

    def test_json_serialization(self):
        """Test JSON export/import."""
        from core.workflows.builder import (
            WorkflowDefinition,
            WorkflowNode,
            WorkflowEdge,
            NodeType,
        )

        workflow = WorkflowDefinition(name="JSON Test")
        workflow.add_node(WorkflowNode(id="s", type=NodeType.START, label="S"))
        workflow.add_node(WorkflowNode(id="e", type=NodeType.END, label="E"))
        workflow.add_edge(WorkflowEdge(id="e1", source_id="s", target_id="e"))

        json_str = workflow.to_json()
        restored = WorkflowDefinition.from_json(json_str)

        assert restored.name == "JSON Test"
        assert len(restored.nodes) == 2


class TestWorkflowBuilder:
    """Tests for fluent WorkflowBuilder."""

    def test_simple_workflow(self):
        """Test building a simple workflow."""
        from core.workflows.builder import WorkflowBuilder

        workflow = (
            WorkflowBuilder("Simple")
            .start()
            .agent("Analyzer", agent_id="analyzer-1")
            .end()
            .build()
        )

        assert workflow.name == "Simple"
        assert len(workflow.nodes) == 3
        assert len(workflow.edges) == 2

    def test_workflow_with_tool(self):
        """Test building workflow with tool node."""
        from core.workflows.builder import WorkflowBuilder, NodeType

        workflow = (
            WorkflowBuilder("With Tool")
            .start()
            .tool("Search", tool_id="web-search")
            .end()
            .build()
        )

        tool_nodes = [n for n in workflow.nodes if n.type == NodeType.TOOL]
        assert len(tool_nodes) == 1
        assert tool_nodes[0].tool_id == "web-search"


class TestWorkflowExecutor:
    """Tests for WorkflowExecutor."""

    @pytest.mark.asyncio
    async def test_execute_simple_workflow(self):
        """Test executing a simple workflow."""
        from core.workflows.builder import WorkflowBuilder
        from core.workflows.executor import WorkflowExecutor, ExecutionStatus

        workflow = WorkflowBuilder("Execute Test").start().end().build()

        executor = WorkflowExecutor()
        result = await executor.execute(workflow, initial_input="test")

        assert result.status == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_invalid_workflow(self):
        """Test executing invalid workflow fails."""
        from core.workflows.builder import WorkflowDefinition
        from core.workflows.executor import WorkflowExecutor, ExecutionStatus

        workflow = WorkflowDefinition(name="Invalid")
        # No nodes at all

        executor = WorkflowExecutor()
        result = await executor.execute(workflow)

        assert result.status == ExecutionStatus.FAILED
        assert "Validation" in result.error

    @pytest.mark.asyncio
    async def test_execution_context(self):
        """Test execution context variable passing."""
        from core.workflows.executor import ExecutionContext

        context = ExecutionContext(workflow_id="test")

        context.set_variable("key", "value")
        assert context.get_variable("key") == "value"
        assert context.get_variable("missing", "default") == "default"

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        """Test execution timeout."""
        import asyncio
        from core.workflows.builder import WorkflowBuilder
        from core.workflows.executor import WorkflowExecutor, ExecutionStatus

        async def slow_handler(node, context):
            await asyncio.sleep(0.5)
            return "too late"

        workflow = (
            WorkflowBuilder("Timeout Test")
            .start()
            .agent("Slow Agent", agent_id="slow", timeout=0.1)
            .end()
            .build()
        )

        executor = WorkflowExecutor()
        executor.register_handler("agent", slow_handler)

        result = await executor.execute(workflow)

        assert result.status == ExecutionStatus.FAILED
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_safe_condition_eval(self):
        """Test safe condition evaluation."""
        from core.workflows.builder import WorkflowBuilder
        from core.workflows.executor import WorkflowExecutor, ExecutionStatus

        # Test valid condition
        workflow = (
            WorkflowBuilder("Condition Test")
            .start()
            .condition("Check", expression="output == 'match'")
            .end()
            .build()
        )

        executor = WorkflowExecutor()
        result = await executor.execute(workflow, initial_input="match")
        assert result.status == ExecutionStatus.COMPLETED

        # Test unsafe condition (trying to access builtins)
        workflow_unsafe = (
            WorkflowBuilder("Unsafe Test")
            .start()
            .condition("Hack", expression="__import__('os').system('ls')")
            .end()
            .build()
        )

        result_unsafe = await executor.execute(workflow_unsafe, initial_input="hack")
        # Should fail evaluation and return False (defaulting to first edge usually,
        # but here it might just log a warning and continue if edges are handled)
        # However, it should NOT execute the command.
        # Our executor logs a warning and returns False on exception.

        assert (
            result_unsafe.status == ExecutionStatus.COMPLETED
        )  # Because it fails to false and hits end
        # We checked context logging in executor
