"""
Workflow Builder

Visual workflow definition and execution for baselith-cores.
"""

from .adapters import ColonyNodeAdapter, CrewNodeAdapter
from .builder import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode
from .executor import ExecutionContext, WorkflowExecutor, WorkflowResult
from .flow_handler import WorkflowFlowHandler

__all__ = [
    "ColonyNodeAdapter",
    "CrewNodeAdapter",
    "ExecutionContext",
    "NodeType",
    "WorkflowDefinition",
    "WorkflowEdge",
    "WorkflowExecutor",
    "WorkflowFlowHandler",
    "WorkflowNode",
    "WorkflowResult",
]
