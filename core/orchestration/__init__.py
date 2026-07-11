"""
Core Orchestration Package

Provides a generic, domain-agnostic orchestration framework for baselith-cores.
This package contains the base classes and protocols for building orchestrators
that coordinate intent classification, flow handling, and agent execution.

Usage:
    from core.orchestration import (
        Orchestrator,
        IntentClassifier,
        BaseFlowHandler,
        BaseStreamHandler,
    )

For domain-specific extensions, see `app.agents.orchestrator` which provides
backward-compatible implementations with Graph support.
"""

from .adaptive import AdaptiveConfig, AdaptiveController, ProcessingPath

# Autonomy spectrum + approval enforcement
from .autonomy import (
    ApprovalPendingError,
    ApprovalRequiredError,
    AutonomyLevel,
    AutonomyPolicy,
    AutonomyUpgradeGate,
    enforce_approval,
)
from .checkpoint import (
    Checkpoint,
    CheckpointManager,
    CheckpointStore,
    InMemoryCheckpointStore,
    record_approval_decision,
    step_key,
)
from .handlers import BaseFlowHandler, BaseStreamHandler
from .intent_classifier import IntentClassifier
from .orchestrator import Orchestrator

# New efficiency-focused modules
from .parallel import ExecutionPlan, ParallelToolExecutor, ToolCall, ToolResult
from .protocols import (
    AgentProtocol,
    FlowHandler,
    IntentClassifierProtocol,
    OrchestratorProtocol,
    StreamHandler,
)
from .tool_output import truncate_tool_output

__all__ = [
    # Protocols
    "AgentProtocol",
    "FlowHandler",
    "StreamHandler",
    "IntentClassifierProtocol",
    "OrchestratorProtocol",
    # Implementations
    "Orchestrator",
    "IntentClassifier",
    "BaseFlowHandler",
    "BaseStreamHandler",
    # Parallel Execution (NEW)
    "ParallelToolExecutor",
    "ToolCall",
    "ToolResult",
    "ExecutionPlan",
    # Durable checkpointing / resume
    "Checkpoint",
    "CheckpointManager",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "step_key",
    # Tool output hygiene
    "truncate_tool_output",
    # Adaptive Control (NEW)
    "AdaptiveController",
    "ProcessingPath",
    "AdaptiveConfig",
    # Autonomy
    "ApprovalPendingError",
    "ApprovalRequiredError",
    "AutonomyLevel",
    "AutonomyPolicy",
    "AutonomyUpgradeGate",
    "enforce_approval",
    "record_approval_decision",
]
