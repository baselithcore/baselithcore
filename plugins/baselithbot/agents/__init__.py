"""Multi-agent routing within Baselithbot."""

from plugins.baselithbot.agents.custom import (
    ACTION_CATALOG,
    AgentActionDescriptor,
    AgentActionSpec,
    CustomAgentRegistry,
    CustomAgentSpec,
    CustomAgentStore,
)
from plugins.baselithbot.agents.registry import AgentEntry, AgentInvoker, AgentRegistry
from plugins.baselithbot.agents.router import AgentRouter, RoutingDecision

__all__ = [
    "ACTION_CATALOG",
    "AgentActionDescriptor",
    "AgentActionSpec",
    "AgentEntry",
    "AgentInvoker",
    "AgentRegistry",
    "AgentRouter",
    "CustomAgentRegistry",
    "CustomAgentSpec",
    "CustomAgentStore",
    "RoutingDecision",
]
