"""Typed developer-facing Agent API (see :mod:`baselith.core.agent.agent`)."""

from baselith.core.agent._repeat_guard import (
    AgentIterationLimitError,
    AgentLoopError,
    RepeatedToolCallError,
)
from baselith.core.agent.agent import Agent, AgentOutputValidationError, AgentResult
from baselith.core.agent.crew import (
    AgentUsage,
    CostFn,
    Crew,
    CrewResult,
    Task,
    TaskResult,
)
from baselith.core.agent.crew_hierarchical import ReviewDecision, ReviewVerdict
from baselith.core.agent.events import (
    AgentEvent,
    Completed,
    Failed,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
)
from baselith.core.agent.group_chat import (
    CapabilitySelector,
    ChatMessage,
    GroupChat,
    GroupChatResult,
    LLMManagerSelector,
    Participant,
    RoundRobinSelector,
    SpeakerSelector,
)

__all__ = [
    "Agent",
    "AgentIterationLimitError",
    "AgentLoopError",
    "AgentEvent",
    "AgentOutputValidationError",
    "AgentResult",
    "AgentUsage",
    "CapabilitySelector",
    "ChatMessage",
    "Completed",
    "CostFn",
    "Crew",
    "CrewResult",
    "Failed",
    "GroupChat",
    "GroupChatResult",
    "LLMManagerSelector",
    "Participant",
    "RepeatedToolCallError",
    "ReviewDecision",
    "ReviewVerdict",
    "RoundRobinSelector",
    "SpeakerSelector",
    "Task",
    "TaskResult",
    "TextDelta",
    "ToolCallFinished",
    "ToolCallStarted",
]
