"""BaselithbotPlugin — registration entry point for the BaselithCore framework."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from core.observability.logging import get_logger
from core.plugins import AgentPlugin, RouterPlugin

from .agent import BaselithbotAgent
from .agents import AgentRegistry
from .canvas import CanvasSurface
from .channels import ChannelRegistry, build_default_registry
from .chat_commands import ChatCommandRouter
from .computer_tools import build_computer_tool_definitions
from .computer_use import ComputerUseConfig
from .cron import CronScheduler
from .extra_tools import build_extra_tool_definitions
from .handlers import BaselithbotFlowHandler
from .nodes import NodePairing
from .openclaw_tools import build_openclaw_tool_definitions
from .router import create_router
from .sessions import SessionManager
from .skills import SkillRegistry
from .tools import build_baselithbot_tool_definitions
from .types import StealthConfig
from .usage import UsageLedger
from .workspace import WorkspaceManager

logger = get_logger(__name__)


class BaselithbotPlugin(AgentPlugin, RouterPlugin):
    """Plugin exposing Baselithbot autonomous browser navigation."""

    def __init__(self) -> None:
        super().__init__()
        self._agent: BaselithbotAgent | None = None
        self._agent_config: dict[str, Any] = {}
        self._flow_handler: BaselithbotFlowHandler = BaselithbotFlowHandler(self)
        self._channels: ChannelRegistry = build_default_registry()
        self._sessions: SessionManager = SessionManager()
        self._chat_commands: ChatCommandRouter = ChatCommandRouter()
        self._skills: SkillRegistry = SkillRegistry()
        self._cron: CronScheduler = CronScheduler()
        self._pairing: NodePairing = NodePairing()
        self._canvas: CanvasSurface = CanvasSurface()
        self._usage: UsageLedger = UsageLedger()
        self._workspaces: WorkspaceManager = WorkspaceManager()
        self._agent_registry: AgentRegistry = AgentRegistry()

    @property
    def agent(self) -> BaselithbotAgent | None:
        """Return the active agent instance, if any."""
        return self._agent

    async def initialize(self, config: dict[str, Any]) -> None:
        """Cache config; defer agent startup until first use."""
        await super().initialize(config)
        self._agent_config = config or {}
        if "stealth" in self._agent_config and not isinstance(
            self._agent_config["stealth"], StealthConfig
        ):
            self._agent_config["stealth"] = StealthConfig(
                **self._agent_config["stealth"]
            )
        if "computer_use" in self._agent_config and not isinstance(
            self._agent_config["computer_use"], ComputerUseConfig
        ):
            self._agent_config["computer_use"] = ComputerUseConfig(
                **self._agent_config["computer_use"]
            )
        logger.info("baselithbot_plugin_initialized", config_keys=list(config.keys()))

    async def shutdown(self) -> None:
        """Stop the active agent + cron + channels, then call super."""
        if self._agent is not None:
            await self._agent.shutdown()
            self._agent = None
        try:
            await self._cron.stop()
        except Exception as exc:
            logger.warning("baselithbot_cron_stop_failed", error=str(exc))
        try:
            await self._channels.shutdown_all()
        except Exception as exc:
            logger.warning("baselithbot_channels_shutdown_failed", error=str(exc))
        await super().shutdown()

    def create_agent(self, service: Any = None, **kwargs: Any) -> BaselithbotAgent:
        """Factory invoked by the orchestrator.

        Args:
            service: ChatService-like dependency required by ``AgentPlugin``
                contract; Baselithbot is self-contained and does not consume it.
            **kwargs: Optional overrides merged onto the cached plugin config.
        """
        del service
        merged: dict[str, Any] = {**self._agent_config, **kwargs}
        return BaselithbotAgent(config=merged)

    async def get_or_start_agent(self) -> BaselithbotAgent:
        """Return the singleton agent, starting it on first call."""
        if self._agent is None:
            new_agent = self.create_agent()
            await new_agent.startup()
            self._agent = new_agent
        return self._agent

    def create_router(self) -> APIRouter:
        """Create the FastAPI router exposing /run and /status."""
        return create_router(self)

    def get_intent_patterns(self) -> list[dict[str, Any]]:
        """Intent patterns triggering Baselithbot dispatch."""
        return [
            {
                "name": "baselithbot_browse",
                "patterns": [
                    "baselithbot",
                    "browse autonomously",
                    "navigate web",
                    "automate browser",
                    "scrape stealth",
                    "stealth browse",
                ],
                "handler": "handle_browse",
                "priority": 110,
            },
        ]

    def get_mcp_tools(self) -> list[dict[str, Any]]:
        """Expose Baselithbot tools (browser + Computer Use + OpenClaw) to MCP."""
        browser_tools = build_baselithbot_tool_definitions(
            agent_factory=lambda: self.create_agent()
        )
        cu_config = self._agent_config.get("computer_use")
        if cu_config is None:
            cu_config = ComputerUseConfig()
        elif not isinstance(cu_config, ComputerUseConfig):
            cu_config = ComputerUseConfig(**cu_config)
        computer_tools = build_computer_tool_definitions(cu_config)
        openclaw_tools = build_openclaw_tool_definitions(
            channels=self._channels,
            sessions=self._sessions,
            chat_commands=self._chat_commands,
            skills=self._skills,
            cron=self._cron,
            pairing=self._pairing,
            canvas=self._canvas,
        )
        extra_tools = build_extra_tool_definitions(
            config=cu_config,
            usage=self._usage,
            workspaces=self._workspaces,
            agents=self._agent_registry,
        )
        return [*browser_tools, *computer_tools, *openclaw_tools, *extra_tools]

    @property
    def channels(self) -> ChannelRegistry:
        return self._channels

    @property
    def sessions(self) -> SessionManager:
        return self._sessions

    @property
    def chat_commands(self) -> ChatCommandRouter:
        return self._chat_commands

    @property
    def skills(self) -> SkillRegistry:
        return self._skills

    @property
    def cron(self) -> CronScheduler:
        return self._cron

    @property
    def pairing(self) -> NodePairing:
        return self._pairing

    @property
    def canvas(self) -> CanvasSurface:
        return self._canvas

    @property
    def usage(self) -> UsageLedger:
        return self._usage

    @property
    def workspaces(self) -> WorkspaceManager:
        return self._workspaces

    @property
    def agent_registry(self) -> AgentRegistry:
        return self._agent_registry

    def get_flow_handlers(self) -> dict[str, Any]:
        """Bind intent names to flow handler coroutines."""
        return {
            "baselithbot_browse": self._flow_handler.handle_browse,
        }


__all__ = ["BaselithbotPlugin"]
