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
from .checkpoint_history import fork_run, get_state, get_state_history, list_runs
from .handlers import BaseFlowHandler, BaseStreamHandler
from .intent_classifier import IntentClassifier
from .modality_router import Modality, annotate_context, detect_modality
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
from .run_events import (
    RunEventStream,
    get_run_event_stream,
    publish_run_event,
    set_run_event_broadcaster,
    stream_run_events,
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
    "list_runs",
    "step_key",
    # State history / time-travel
    "fork_run",
    "get_state",
    "get_state_history",
    # Structured run-event streaming
    "RunEventStream",
    "get_run_event_stream",
    "publish_run_event",
    "set_run_event_broadcaster",
    "stream_run_events",
    # Tool output hygiene
    "truncate_tool_output",
    # Modality routing
    "Modality",
    "annotate_context",
    "detect_modality",
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
