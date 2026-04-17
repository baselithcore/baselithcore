"""Baselithbot plugin package.

Autonomous web navigation agent inspired by OpenClaw, layered on top of the
existing ``browser-agent`` Playwright backend. Adds stealth mode, sanitized
JS execution, and an explicit Observe -> Plan -> Act cognitive loop.
"""

from .agent import BaselithbotAgent
from .agents import AgentEntry, AgentRegistry, AgentRouter
from .canvas import A2UIRenderer, CanvasSurface
from .channels import (
    SUPPORTED_CHANNELS,
    ChannelAdapter,
    ChannelMessage,
    ChannelRegistry,
)
from .chat_commands import SUPPORTED_COMMANDS, ChatCommandRouter
from .computer_use import AuditLogger, ComputerUseConfig, ComputerUseError
from .cron import CronScheduler
from .model_routing import (
    AuthProfile,
    AuthProfilePool,
    FailoverPolicy,
    ModelRouter,
    ProviderConfig,
)
from .nodes import NodePairing
from .plugin import BaselithbotPlugin
from .sessions import Session, SessionManager, SessionMessage
from .skills import Skill, SkillRegistry, SkillScope
from .types import BaselithbotResult, BaselithbotTask, StealthConfig
from .usage import UsageEvent, UsageLedger
from .workspace import Workspace, WorkspaceConfig, WorkspaceManager

__all__ = [
    "BaselithbotAgent",
    "BaselithbotPlugin",
    "BaselithbotResult",
    "BaselithbotTask",
    "StealthConfig",
    "ComputerUseConfig",
    "ComputerUseError",
    "AuditLogger",
    "ChannelAdapter",
    "ChannelMessage",
    "ChannelRegistry",
    "SUPPORTED_CHANNELS",
    "Session",
    "SessionManager",
    "SessionMessage",
    "ChatCommandRouter",
    "SUPPORTED_COMMANDS",
    "Skill",
    "SkillRegistry",
    "SkillScope",
    "CronScheduler",
    "NodePairing",
    "CanvasSurface",
    "A2UIRenderer",
    "UsageEvent",
    "UsageLedger",
    "Workspace",
    "WorkspaceConfig",
    "WorkspaceManager",
    "AgentEntry",
    "AgentRegistry",
    "AgentRouter",
    "AuthProfile",
    "AuthProfilePool",
    "FailoverPolicy",
    "ProviderConfig",
    "ModelRouter",
]
