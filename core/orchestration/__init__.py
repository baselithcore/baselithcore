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

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from core._lazy import lazy_exports

if TYPE_CHECKING:  # pragma: no cover - the eager view, for type checkers
    from .adaptive import (
        AdaptiveConfig,
        AdaptiveController,
        ProcessingPath,
    )
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
        step_key,
    )
    from .checkpoint_approvals import (
        ApprovalPrincipal,
        record_approval_decision,
    )
    from .checkpoint_history import (
        fork_run,
        get_state,
        get_state_history,
        list_runs,
    )
    from .handlers import (
        BaseFlowHandler,
        BaseStreamHandler,
    )
    from .intent_classifier import (
        IntentClassifier,
    )
    from .modality_router import (
        Modality,
        annotate_context,
        detect_modality,
    )
    from .orchestrator import (
        BUILTIN_INTENTS,
        Orchestrator,
    )
    from .parallel import (
        ExecutionPlan,
        ParallelToolExecutor,
        ToolCall,
        ToolResult,
    )
    from .protocols import (
        AgentProtocol,
        FlowHandler,
        IntentClassifierProtocol,
        OrchestratorProtocol,
        StreamHandler,
    )
    from .recovery import (
        recovery_sweep_loop,
        run_recovery_cycle,
    )
    from .run_events import (
        RunEventStream,
        get_run_event_stream,
        publish_run_event,
        set_run_event_broadcaster,
        stream_run_events,
    )
    from .tool_output import (
        UNTRUSTED_OUTPUT_SYSTEM_RULE,
        escape_untrusted_markers,
        sanitize_tool_output,
        truncate_tool_output,
        unwrap_untrusted,
        wrap_untrusted,
    )

#: ``exported name -> submodule`` (``submodule:original`` where the package
#: renames on the way out). Resolved on first access by :func:`lazy_exports`,
#: so importing one name no longer costs the whole package. Keep it in step
#: with ``__all__`` below; ``test_lazy_package_exports`` enforces that.
_EXPORTS: Final[dict[str, str]] = {
    "AdaptiveConfig": "adaptive",
    "AdaptiveController": "adaptive",
    "AgentProtocol": "protocols",
    "ApprovalPendingError": "autonomy",
    "ApprovalPrincipal": "checkpoint_approvals",
    "ApprovalRequiredError": "autonomy",
    "AutonomyLevel": "autonomy",
    "AutonomyPolicy": "autonomy",
    "AutonomyUpgradeGate": "autonomy",
    "BUILTIN_INTENTS": "orchestrator",
    "BaseFlowHandler": "handlers",
    "BaseStreamHandler": "handlers",
    "Checkpoint": "checkpoint",
    "CheckpointManager": "checkpoint",
    "CheckpointStore": "checkpoint",
    "ExecutionPlan": "parallel",
    "FlowHandler": "protocols",
    "InMemoryCheckpointStore": "checkpoint",
    "IntentClassifier": "intent_classifier",
    "IntentClassifierProtocol": "protocols",
    "Modality": "modality_router",
    "Orchestrator": "orchestrator",
    "OrchestratorProtocol": "protocols",
    "ParallelToolExecutor": "parallel",
    "ProcessingPath": "adaptive",
    "RunEventStream": "run_events",
    "StreamHandler": "protocols",
    "ToolCall": "parallel",
    "ToolResult": "parallel",
    "UNTRUSTED_OUTPUT_SYSTEM_RULE": "tool_output",
    "annotate_context": "modality_router",
    "detect_modality": "modality_router",
    "enforce_approval": "autonomy",
    "escape_untrusted_markers": "tool_output",
    "fork_run": "checkpoint_history",
    "get_run_event_stream": "run_events",
    "get_state": "checkpoint_history",
    "get_state_history": "checkpoint_history",
    "list_runs": "checkpoint_history",
    "publish_run_event": "run_events",
    "record_approval_decision": "checkpoint_approvals",
    "recovery_sweep_loop": "recovery",
    "run_recovery_cycle": "recovery",
    "sanitize_tool_output": "tool_output",
    "set_run_event_broadcaster": "run_events",
    "step_key": "checkpoint",
    "stream_run_events": "run_events",
    "truncate_tool_output": "tool_output",
    "unwrap_untrusted": "tool_output",
    "wrap_untrusted": "tool_output",
}

__getattr__ = lazy_exports(__name__, _EXPORTS)


def __dir__() -> list[str]:
    """The public names, so ``dir()`` sees the ones not yet resolved."""
    return sorted(__all__)


__all__ = [
    # Protocols
    "AgentProtocol",
    "FlowHandler",
    "StreamHandler",
    "IntentClassifierProtocol",
    "OrchestratorProtocol",
    # Implementations
    "BUILTIN_INTENTS",
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
    "UNTRUSTED_OUTPUT_SYSTEM_RULE",
    "escape_untrusted_markers",
    "sanitize_tool_output",
    "truncate_tool_output",
    "unwrap_untrusted",
    "wrap_untrusted",
    # Crash recovery
    "recovery_sweep_loop",
    "run_recovery_cycle",
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
    "ApprovalPrincipal",
    "record_approval_decision",
]
