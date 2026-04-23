"""Baselithbot plugin package.

Autonomous web navigation agent inspired by OpenClaw, layered on top of the
existing ``browser-agent`` Playwright backend. Adds stealth mode, sanitized
JS execution, and an explicit Observe -> Plan -> Act cognitive loop.
"""

from plugins.baselithbot.agents import AgentEntry, AgentRegistry, AgentRouter
from plugins.baselithbot.browser.agent import BaselithbotAgent
from plugins.baselithbot.canvas import A2UIRenderer, CanvasSurface
from plugins.baselithbot.channels import (
    SUPPORTED_CHANNELS,
    ChannelAdapter,
    ChannelMessage,
    ChannelRegistry,
)
from plugins.baselithbot.chat.commands import SUPPORTED_COMMANDS, ChatCommandRouter
from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig, ComputerUseError
from plugins.baselithbot.cron.scheduler import CronScheduler
from plugins.baselithbot.model_routing import (
    AuthProfile,
    AuthProfilePool,
    FailoverPolicy,
    ModelRouter,
    ProviderConfig,
)
from plugins.baselithbot.models import BaselithbotResult, BaselithbotTask, StealthConfig
from plugins.baselithbot.nodes import NodePairing
from plugins.baselithbot.observability.usage import UsageEvent, UsageLedger
from plugins.baselithbot.plugin import BaselithbotPlugin
from plugins.baselithbot.sessions import Session, SessionManager, SessionMessage
from plugins.baselithbot.skills import Skill, SkillRegistry, SkillScope
from plugins.baselithbot.workspace import Workspace, WorkspaceConfig, WorkspaceManager

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
