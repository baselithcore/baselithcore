"""
Workflow Builder

Define and serialize baselith-core workflows as graphs.
"""

import json
from core.observability.logging import get_logger
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = get_logger(__name__)


class NodeType(str, Enum):
    """Types of workflow nodes."""

    START = "start"  # Entry point
    END = "end"  # Exit point
    AGENT = "agent"  # AI agent execution
    TOOL = "tool"  # Tool invocation
    CONDITION = "condition"  # Conditional branching
    PARALLEL = "parallel"  # Parallel execution
    MERGE = "merge"  # Merge parallel branches
    LOOP = "loop"  # Loop construct
    HUMAN = "human"  # Human-in-the-loop
    TRANSFORM = "transform"  # Data transformation


@dataclass
class NodePosition:
    """Position of a node in the visual editor."""

    x: float = 0.0
    y: float = 0.0


@dataclass
class WorkflowNode:
    """A single node in the workflow graph."""

    id: str
    type: NodeType
    label: str
    config: Dict[str, Any] = field(default_factory=dict)
    position: NodePosition = field(default_factory=NodePosition)
    timeout: Optional[float] = None  # Timeout in seconds

    # For agent/tool nodes
    agent_id: Optional[str] = None
    tool_id: Optional[str] = None

    # For condition nodes
    condition_expression: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "type": self.type.value,
            "label": self.label,
            "config": self.config,
            "position": {"x": self.position.x, "y": self.position.y},
            "agent_id": self.agent_id,
            "tool_id": self.tool_id,
            "condition_expression": self.condition_expression,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowNode":
        """Deserialize from dictionary."""
        pos_data = data.get("position", {})
        return cls(
            id=data["id"],
            type=NodeType(data["type"]),
            label=data.get("label", ""),
            config=data.get("config", {}),
            position=NodePosition(x=pos_data.get("x", 0), y=pos_data.get("y", 0)),
            agent_id=data.get("agent_id"),
            tool_id=data.get("tool_id"),
            condition_expression=data.get("condition_expression"),
            timeout=data.get("timeout"),
        )


@dataclass
class WorkflowEdge:
    """An edge connecting two nodes."""

    id: str
    source_id: str
    target_id: str
    condition_label: Optional[str] = None  # For conditional edges

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "condition_label": self.condition_label,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowEdge":
        """Deserialize from dictionary."""
        return cls(
            id=data["id"],
            source_id=data["source_id"],
            target_id=data["target_id"],
            condition_label=data.get("condition_label"),
        )


@dataclass
class WorkflowDefinition:
    """Complete workflow definition."""

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Untitled Workflow"
    description: str = ""
    version: str = "1.0.0"
    nodes: List[WorkflowNode] = field(default_factory=list)
    edges: List[WorkflowEdge] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_node(self, node: WorkflowNode) -> None:
        """Add a node to the workflow."""
        self.nodes.append(node)
        self.updated_at = datetime.now(timezone.utc)

    def add_edge(self, edge: WorkflowEdge) -> None:
        """Add an edge to the workflow."""
        self.edges.append(edge)
        self.updated_at = datetime.now(timezone.utc)

    def get_node(self, node_id: str) -> Optional[WorkflowNode]:
        """Get a node by ID."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def get_start_node(self) -> Optional[WorkflowNode]:
        """Get the start node."""
        for node in self.nodes:
            if node.type == NodeType.START:
                return node
        return None

    def get_outgoing_edges(self, node_id: str) -> List[WorkflowEdge]:
        """Get all edges originating from a node."""
        return [e for e in self.edges if e.source_id == node_id]

    def get_incoming_edges(self, node_id: str) -> List[WorkflowEdge]:
        """Get all edges pointing to a node."""
        return [e for e in self.edges if e.target_id == node_id]

    def validate(self) -> List[str]:
        """
        Validate the workflow structure.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        # Check for start node
        start_nodes = [n for n in self.nodes if n.type == NodeType.START]
        if len(start_nodes) == 0:
            errors.append("Workflow must have a START node")
        elif len(start_nodes) > 1:
            errors.append("Workflow must have exactly one START node")

        # Check for end node
        end_nodes = [n for n in self.nodes if n.type == NodeType.END]
        if len(end_nodes) == 0:
            errors.append("Workflow must have at least one END node")

        # Check all edges reference valid nodes
        node_ids = {n.id for n in self.nodes}
        for edge in self.edges:
            if edge.source_id not in node_ids:
                errors.append(
                    f"Edge {edge.id} references unknown source: {edge.source_id}"
                )
            if edge.target_id not in node_ids:
                errors.append(
                    f"Edge {edge.id} references unknown target: {edge.target_id}"
                )

        # Check agent nodes have agent_id
        for node in self.nodes:
            if node.type == NodeType.AGENT and not node.agent_id:
                errors.append(f"Agent node {node.id} must have agent_id")

        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowDefinition":
        """Deserialize from dictionary."""
        workflow = cls(
            id=data.get("id", str(uuid4())),
            name=data.get("name", "Untitled"),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            metadata=data.get("metadata", {}),
        )
        workflow.nodes = [WorkflowNode.from_dict(n) for n in data.get("nodes", [])]
        workflow.edges = [WorkflowEdge.from_dict(e) for e in data.get("edges", [])]
        return workflow

    def to_json(self) -> str:
        """Export as JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "WorkflowDefinition":
        """Import from JSON string."""
        return cls.from_dict(json.loads(json_str))


class WorkflowBuilder:
    """
    Fluent builder for creating workflows.

    Example:
        workflow = (
            WorkflowBuilder("My Workflow")
            .start()
            .agent("analyzer", agent_id="analysis-agent")
            .condition("check_result", expression="output.score > 0.8")
            .agent("refiner", agent_id="refinement-agent")
            .end()
            .build()
        )
    """

    def __init__(self, name: str = "Untitled"):
        """Initialize builder."""
        self._workflow = WorkflowDefinition(name=name)
        self._last_node_id: Optional[str] = None
        self._node_counter = 0

    def _next_id(self) -> str:
        """Generate next node ID."""
        self._node_counter += 1
        return f"node_{self._node_counter}"

    def _add_node(
        self,
        node_type: NodeType,
        label: str,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> "WorkflowBuilder":
        """Add a node and connect from previous."""
        node_id = self._next_id()
        node = WorkflowNode(
            id=node_id, type=node_type, label=label, timeout=timeout, **kwargs
        )
        self._workflow.add_node(node)

        # Auto-connect from previous node
        if self._last_node_id:
            edge_id = f"edge_{self._node_counter}"
            edge = WorkflowEdge(
                id=edge_id,
                source_id=self._last_node_id,
                target_id=node_id,
            )
            self._workflow.add_edge(edge)

        self._last_node_id = node_id
        return self

    def start(self) -> "WorkflowBuilder":
        """Add start node."""
        return self._add_node(NodeType.START, "Start")

    def end(self) -> "WorkflowBuilder":
        """Add end node."""
        return self._add_node(NodeType.END, "End")

    def agent(
        self, label: str, agent_id: str, timeout: Optional[float] = None, **config: Any
    ) -> "WorkflowBuilder":
        """Add agent node."""
        return self._add_node(
            NodeType.AGENT, label, agent_id=agent_id, timeout=timeout, config=config
        )

    def tool(self, label: str, tool_id: str, **config: Any) -> "WorkflowBuilder":
        """Add tool node."""
        return self._add_node(NodeType.TOOL, label, tool_id=tool_id, config=config)

    def condition(self, label: str, expression: str) -> "WorkflowBuilder":
        """Add condition node."""
        return self._add_node(
            NodeType.CONDITION, label, condition_expression=expression
        )

    def transform(self, label: str, **config: Any) -> "WorkflowBuilder":
        """Add transform node."""
        return self._add_node(NodeType.TRANSFORM, label, config=config)

    def parallel(self, label: str = "Parallel") -> "WorkflowBuilder":
        """Add parallel execution node."""
        return self._add_node(NodeType.PARALLEL, label)

    def merge(self, label: str = "Merge") -> "WorkflowBuilder":
        """Add merge node."""
        return self._add_node(NodeType.MERGE, label)

    def build(self) -> WorkflowDefinition:
        """Build and return the workflow."""
        return self._workflow
