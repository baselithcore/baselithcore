from core.workflows.builder import (
    WorkflowBuilder,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowEdge,
    NodeType,
    NodePosition,
)


def test_workflow_node_serialization():
    node = WorkflowNode(
        id="n1",
        type=NodeType.AGENT,
        label="Test Agent",
        agent_id="my-agent",
        config={"key": "value"},
        position=NodePosition(x=10, y=20),
        timeout=30.0,
    )

    data = node.to_dict()
    assert data["id"] == "n1"
    assert data["type"] == "agent"
    assert data["agent_id"] == "my-agent"
    assert data["config"] == {"key": "value"}
    assert data["position"]["x"] == 10

    node2 = WorkflowNode.from_dict(data)
    assert node2.id == node.id
    assert node2.type == node.type
    assert node2.position.y == 20
    assert node2.timeout == 30.0


def test_workflow_edge_serialization():
    edge = WorkflowEdge(id="e1", source_id="n1", target_id="n2", condition_label="true")
    data = edge.to_dict()
    assert data["id"] == "e1"
    assert data["condition_label"] == "true"

    edge2 = WorkflowEdge.from_dict(data)
    assert edge2.id == edge.id
    assert edge2.source_id == "n1"


def test_workflow_definition_serialization():
    wf = WorkflowDefinition(name="Test Work")
    wf.add_node(WorkflowNode(id="n1", type=NodeType.START, label="Start"))
    wf.add_node(WorkflowNode(id="n2", type=NodeType.END, label="End"))
    wf.add_edge(WorkflowEdge(id="e1", source_id="n1", target_id="n2"))

    json_str = wf.to_json()
    wf2 = WorkflowDefinition.from_json(json_str)

    assert wf2.name == "Test Work"
    assert len(wf2.nodes) == 2
    assert len(wf2.edges) == 1
    assert wf2.get_start_node().id == "n1"


def test_workflow_validation():
    # Empty workflow
    wf = WorkflowDefinition()
    errors = wf.validate()
    assert "Workflow must have a START node" in errors
    assert "Workflow must have at least one END node" in errors

    # Missing agent_id
    wf.add_node(WorkflowNode(id="n1", type=NodeType.START, label="Start"))
    wf.add_node(WorkflowNode(id="n2", type=NodeType.AGENT, label="Agent"))
    wf.add_node(WorkflowNode(id="n3", type=NodeType.END, label="End"))
    errors = wf.validate()
    assert "Agent node n2 must have agent_id" in errors

    # Invalid edge references
    wf.add_edge(WorkflowEdge(id="e1", source_id="n1", target_id="ghost"))
    errors = wf.validate()
    assert "Edge e1 references unknown target: ghost" in errors


def test_workflow_builder():
    builder = WorkflowBuilder("Fluent Workflow")
    # Using builder as intended
    wf = (
        builder.start()
        .agent("Analyze", agent_id="analyst")
        .condition("Check", expression="output.ok")
        .agent("Fix", agent_id="fixer")
        .end()
        .build()
    )

    assert wf.name == "Fluent Workflow"
    assert len(wf.nodes) == 5
    assert len(wf.edges) == 4

    # Check traversal methods
    start = wf.get_start_node()
    assert start.type == NodeType.START

    edges = wf.get_outgoing_edges(start.id)
    assert len(edges) == 1
    assert edges[0].target_id == "node_2"

    back_edges = wf.get_incoming_edges("node_2")
    assert len(back_edges) == 1
    assert back_edges[0].source_id == start.id


def test_builder_additional_nodes():
    builder = WorkflowBuilder()
    wf = (
        builder.start()
        .transform("Scale", scale=2.0)
        .tool("Search", tool_id="google-search")
        .parallel("Branches")
        .merge("Sync")
        .end()
        .build()
    )
    assert len(wf.nodes) == 6
    assert wf.nodes[1].type == NodeType.TRANSFORM
    assert wf.nodes[2].type == NodeType.TOOL
    assert wf.nodes[3].type == NodeType.PARALLEL
    assert wf.nodes[4].type == NodeType.MERGE


def test_workflow_definition_edge_cases():
    wf = WorkflowDefinition()
    assert wf.get_node("missing") is None
    assert wf.get_start_node() is None

    # Multiple starts
    wf.add_node(WorkflowNode(id="s1", type=NodeType.START, label="S1"))
    wf.add_node(WorkflowNode(id="s2", type=NodeType.START, label="S2"))
    wf.add_node(WorkflowNode(id="e1", type=NodeType.END, label="E1"))
    errors = wf.validate()
    assert "Workflow must have exactly one START node" in errors

    # Unknown edge source
    wf.add_edge(WorkflowEdge(id="bad", source_id="unknown", target_id="e1"))
    errors = wf.validate()
    assert any("references unknown source: unknown" in e for e in errors)
