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
    ApprovalRequiredError,
    AutonomyLevel,
    AutonomyPolicy,
    AutonomyUpgradeGate,
    enforce_approval,
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
    # Tool output hygiene
    "truncate_tool_output",
    # Adaptive Control (NEW)
    "AdaptiveController",
    "ProcessingPath",
    "AdaptiveConfig",
    # Autonomy
    "ApprovalRequiredError",
    "AutonomyLevel",
    "AutonomyPolicy",
    "AutonomyUpgradeGate",
    "enforce_approval",
]
