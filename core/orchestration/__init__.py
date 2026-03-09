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

from .protocols import (
    AgentProtocol,
    FlowHandler,
    StreamHandler,
    IntentClassifierProtocol,
    OrchestratorProtocol,
)
from .intent_classifier import IntentClassifier
from .handlers import BaseFlowHandler, BaseStreamHandler
from .orchestrator import Orchestrator

# New efficiency-focused modules
from .parallel import ParallelToolExecutor, ToolCall, ToolResult, ExecutionPlan
from .adaptive import AdaptiveController, ProcessingPath, AdaptiveConfig

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
    # Adaptive Control (NEW)
    "AdaptiveController",
    "ProcessingPath",
    "AdaptiveConfig",
]
