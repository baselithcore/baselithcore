"""BaselithbotPlugin — registration entry point for the BaselithCore framework."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .desktop_agent import DesktopAgent

from fastapi import APIRouter

from core.observability.logging import get_logger
from core.plugins import AgentPlugin, RouterPlugin
from core.services.vision.service import (
    register_api_key_resolver,
    unregister_api_key_resolver,
)

from . import _bootstrap
from .agent import BaselithbotAgent
from .agents import AgentRegistry, CustomAgentRegistry, CustomAgentStore
from .approvals import ApprovalGate
from .canvas import CanvasSurface
from .channels import ChannelRegistry, build_default_registry
from .channels.config_store import ChannelConfigStore
from .chat_commands import ChatCommandRouter
from ._mcp import collect_mcp_tools
from .computer_tools import build_computer_tool_definitions
from .computer_use import ComputerUseConfig
from .cron import CronScheduler
from .cron_custom import CustomCronRegistry, CustomCronStore
from .desktop_lane import DesktopLaneState
from .handlers import BaselithbotFlowHandler
from .inbound import InboundDispatcher, register_default_inbound_handlers
from .model_config import ModelPreferenceStore
from .nodes import NodePairing
from .policies import DMPairingPolicy
from .run_tracker import RunTaskTracker
from .replay import TaskReplayStore
from .router import create_router
from .runtime_config import RuntimeConfigStore
from .secret_store import ProviderSecretStore
from .sessions import SessionManager
from .skills import ClawHubClient, ClawHubConfig, SkillRegistry, SkillScope

if TYPE_CHECKING:
    from .skills import LocalSkillSpec, SkillDraft
from .slash_defaults import SlashRuntimeState, install_default_handlers
from .types import StealthConfig
from .usage import UsageLedger
from .workspace import WorkspaceConfig, WorkspaceManager, WorkspaceStore

logger = get_logger(__name__)


class BaselithbotPlugin(AgentPlugin, RouterPlugin):
    """Plugin exposing Baselithbot autonomous browser navigation."""

    def __init__(self, *, state_dir: str | None = None) -> None:
        super().__init__()
        self._state_dir = state_dir or self._default_state_dir()
        self._agent: BaselithbotAgent | None = None
        self._agent_config: dict[str, Any] = {}
        self._flow_handler: BaselithbotFlowHandler = BaselithbotFlowHandler(self)
        self._channels: ChannelRegistry = build_default_registry()
        self._sessions: SessionManager = SessionManager()
        self._chat_commands: ChatCommandRouter = ChatCommandRouter()
        self._skills: SkillRegistry = SkillRegistry()
        self._cron: CronScheduler = CronScheduler()
        self._custom_crons: CustomCronRegistry = CustomCronRegistry(
            scheduler=self._cron,
            store=CustomCronStore(Path(self._state_dir) / "custom_crons.json"),
            chat_commands=self._chat_commands,
        )
        self._pairing: NodePairing = NodePairing()
        self._run_tracker: RunTaskTracker = RunTaskTracker()
        self._desktop_run_tracker: RunTaskTracker = RunTaskTracker()
        self._desktop_lane: DesktopLaneState = DesktopLaneState()
        self._canvas: CanvasSurface = CanvasSurface()
        self._usage: UsageLedger = UsageLedger()
        self._workspaces: WorkspaceManager = WorkspaceManager(
            store=WorkspaceStore(Path(self._state_dir) / "workspaces.json")
        )
        loaded_ws = self._workspaces.bootstrap()
        if loaded_ws == 0:
            self._workspaces.create(
                WorkspaceConfig(
                    name="default",
                    description="Default workspace (auto-created)",
                    primary=True,
                )
            )
        self._agent_registry: AgentRegistry = AgentRegistry()
        self._custom_agents: CustomAgentRegistry = CustomAgentRegistry(
            agents=self._agent_registry,
            store=CustomAgentStore(Path(self._state_dir) / "custom_agents.json"),
            chat_commands=self._chat_commands,
        )
        self._inbound: InboundDispatcher = InboundDispatcher()
        self._dm_policy: DMPairingPolicy = DMPairingPolicy()
        self._model_prefs: ModelPreferenceStore = ModelPreferenceStore(
            path=self._default_prefs_path()
        )
        self._secret_store: ProviderSecretStore = ProviderSecretStore(
            state_dir=self._state_dir
        )
        self._channel_configs: ChannelConfigStore = ChannelConfigStore(
            state_dir=self._state_dir
        )
        self._runtime_config: RuntimeConfigStore = RuntimeConfigStore(self._state_dir)
        self._approvals: ApprovalGate = ApprovalGate()
        self._replay: TaskReplayStore = TaskReplayStore(
            Path(self._state_dir) / "replay.sqlite"
        )
        self._clawhub: ClawHubClient = ClawHubClient(
            ClawHubConfig(install_dir=str(Path(self._state_dir) / "clawhub"))
        )
        self._workspace_skill_reports: list[dict[str, Any]] = []
        _bootstrap.register_bundled_skills(self)
        _bootstrap.discover_workspace_skills(self)
        _bootstrap.register_default_agents(self)
        register_default_inbound_handlers(self)
        self._slash_state: SlashRuntimeState = install_default_handlers(
            self._chat_commands,
            sessions=self._sessions,
            usage=self._usage,
        )

    @property
    def agent(self) -> BaselithbotAgent | None:
        """Return the active agent instance, if any."""
        return self._agent

    async def initialize(self, config: dict[str, Any]) -> None:
        """Cache config; defer agent startup until first use."""
        await super().initialize(config)
        register_api_key_resolver(self._resolve_provider_key)
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
        await _bootstrap.autostart_enabled_channels(self)
        _bootstrap.register_default_cron_jobs(self)
        loaded_custom = self._custom_crons.bootstrap()
        loaded_agents = self._custom_agents.bootstrap()
        await self._cron.start()
        logger.info(
            "baselithbot_plugin_initialized",
            config_keys=list(config.keys()),
            custom_cron_jobs=loaded_custom,
            custom_agents=loaded_agents,
        )

    def create_workspace_skill(
        self,
        draft: "SkillDraft",
        *,
        workspace: str | None = None,
        overwrite: bool = False,
    ) -> "LocalSkillSpec":
        """Write a custom workspace skill bundle via ``_bootstrap``."""
        return _bootstrap.create_workspace_skill(
            self, draft, workspace=workspace, overwrite=overwrite
        )

    def rescan_workspace_skills(self) -> int:
        """Remove previously registered workspace skills and rescan state_dir."""
        removed = 0
        for existing in list(self._skills.list(SkillScope.WORKSPACE)):
            if self._skills.remove(existing.name):
                removed += 1
        _bootstrap.discover_workspace_skills(self)
        return removed

    def workspace_skill_reports(self) -> list[dict[str, Any]]:
        return list(self._workspace_skill_reports)

    @property
    def clawhub(self) -> ClawHubClient:
        return self._clawhub

    def configure_clawhub(self, config: ClawHubConfig) -> ClawHubClient:
        """Replace the ClawHub client with a new one using ``config``."""
        self._clawhub = ClawHubClient(config)
        return self._clawhub

    async def shutdown(self) -> None:
        """Stop the active agent + cron + channels, then call super."""
        unregister_api_key_resolver(self._resolve_provider_key)
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

    def _resolve_provider_key(self, provider: str) -> str | None:
        """Callback for VisionService / LLM clients to look up a stored key."""
        try:
            return self._secret_store.get_plaintext(provider)
        except Exception as exc:
            logger.warning(
                "baselithbot_secret_resolve_failed",
                provider=provider,
                error=str(exc),
            )
            return None

    def create_agent(self, service: Any = None, **kwargs: Any) -> BaselithbotAgent:
        """Factory invoked by the orchestrator.

        Args:
            service: ChatService-like dependency required by ``AgentPlugin``
                contract; Baselithbot is self-contained and does not consume it.
            **kwargs: Optional overrides merged onto the cached plugin config.
        """
        del service
        merged: dict[str, Any] = {**self._agent_config, **kwargs}
        merged["computer_use"] = self.effective_computer_use_config()
        merged["stealth"] = self.effective_stealth_config()
        merged.setdefault("vision_service", self._build_vision_service())
        return BaselithbotAgent(config=merged)

    def effective_computer_use_config(self) -> ComputerUseConfig:
        """Return the boot ComputerUse config merged with the runtime overlay."""
        base = self._agent_config.get("computer_use")
        if not isinstance(base, ComputerUseConfig):
            base = ComputerUseConfig(**(base or {}))
        return self._runtime_config.get_computer_use(base)

    def build_computer_tool_map(self) -> dict[str, dict[str, Any]]:
        """Build Computer Use tool definitions keyed by tool name.

        Used by the dashboard desktop panel to invoke a single tool without
        routing through the full MCP surface. Rebuilt on every call so the
        runtime overlay takes effect immediately after a policy save.
        """
        cu_config = self.effective_computer_use_config()
        defs = build_computer_tool_definitions(cu_config, approvals=self._approvals)
        return {d["name"]: d for d in defs}

    def effective_stealth_config(self) -> StealthConfig:
        """Return the boot Stealth config merged with the runtime overlay."""
        base = self._agent_config.get("stealth")
        if not isinstance(base, StealthConfig):
            base = StealthConfig(**(base or {}))
        return self._runtime_config.get_stealth(base)

    @property
    def runtime_config(self) -> RuntimeConfigStore:
        """Persisted overlay store for ComputerUse + Stealth runtime edits."""
        return self._runtime_config

    @property
    def approvals(self) -> ApprovalGate:
        """Human-in-the-loop approval gate shared across Computer Use tools."""
        return self._approvals

    @property
    def replay(self) -> TaskReplayStore:
        """SQLite-backed per-step task replay store for the dashboard."""
        return self._replay

    async def get_or_start_agent(self) -> BaselithbotAgent:
        """Return the singleton agent, starting it on first call."""
        if self._agent is None:
            self._apply_model_preferences()
            new_agent = self.create_agent()
            await new_agent.startup()
            self._agent = new_agent
        return self._agent

    def _apply_model_preferences(self) -> None:
        """Push operator-selected vision prefs into the global VisionConfig."""
        _bootstrap.apply_model_preferences(self)

    async def invalidate_agent(self) -> None:
        """Drop the cached agent so the next run rebuilds with fresh prefs.

        Gracefully shuts the old agent down if one exists. Swallows any
        shutdown errors so an in-flight Playwright hiccup cannot break
        preference updates.
        """
        agent = self._agent
        self._agent = None
        if agent is None:
            return
        try:
            await agent.shutdown()
        except Exception as exc:
            logger.warning(
                "baselithbot_agent_invalidate_shutdown_failed", error=str(exc)
            )

    def _build_vision_service(self) -> Any:
        """Construct a failover-aware VisionService seeded with current prefs."""
        from .vision_failover import FailoverVisionService

        prefs = self._model_prefs.get()
        return FailoverVisionService(
            prefs,
            openai_api_key=self._resolve_provider_key("openai"),
            anthropic_api_key=self._resolve_provider_key("anthropic"),
            google_api_key=self._resolve_provider_key("google"),
        )

    def create_router(self) -> APIRouter:
        """Create the FastAPI router exposing /run and /status."""
        return create_router(self)

    @property
    def model_preferences(self) -> ModelPreferenceStore:
        """Persistent operator-chosen model/provider selection."""
        return self._model_prefs

    @property
    def secret_store(self) -> ProviderSecretStore:
        """Encrypted per-provider API key store (set via dashboard UI)."""
        return self._secret_store

    @property
    def channel_configs(self) -> ChannelConfigStore:
        """Encrypted per-channel configuration store (set via dashboard UI)."""
        return self._channel_configs

    @staticmethod
    def _default_state_dir() -> str:
        """Return the on-disk directory used for plugin-local state."""
        from pathlib import Path

        return str(Path(__file__).resolve().parent / ".state")

    def _default_prefs_path(self) -> str:
        """Return the on-disk path used to persist model preferences."""
        from pathlib import Path

        return str(Path(self._state_dir) / "model_preferences.json")

    def get_router_prefix(self) -> str:
        """Mount the plugin at ``/baselithbot`` (not ``/api/baselithbot``).

        Overrides the framework default so the bundled React dashboard is
        reachable at ``http://<host>/baselithbot/`` — a UI-friendly URL,
        not a JSON API namespace.
        """
        return "/baselithbot"

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
        return collect_mcp_tools(self)

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
    def custom_crons(self) -> CustomCronRegistry:
        return self._custom_crons

    @property
    def pairing(self) -> NodePairing:
        return self._pairing

    @property
    def canvas(self) -> CanvasSurface:
        return self._canvas

    @property
    def run_tracker(self) -> RunTaskTracker:
        return self._run_tracker

    @property
    def desktop_run_tracker(self) -> RunTaskTracker:
        """Live + recent desktop-agent runs (separate from browser runs)."""
        return self._desktop_run_tracker

    def create_desktop_agent(self) -> "DesktopAgent":
        """Build a DesktopAgent bound to the current policy + vision service."""
        from .desktop_agent import DesktopAgent

        return DesktopAgent(
            vision=self._build_vision_service(),
            tools=self.build_computer_tool_map(),
            policy=self.effective_computer_use_config(),
        )

    async def register_desktop_cancel(self, run_id: str) -> asyncio.Event:
        """Register (and return) a fresh cancel event for a desktop run."""
        return await self._desktop_lane.register_cancel(run_id)

    async def cancel_desktop_run(self, run_id: str) -> bool:
        """Signal the desktop run to stop at the next loop iteration."""
        return await self._desktop_lane.cancel_run(run_id)

    async def clear_desktop_cancel(self, run_id: str) -> None:
        """Remove the cancel event for a finished run (idempotent)."""
        await self._desktop_lane.clear_cancel(run_id)

    @property
    def desktop_run_lane(self) -> asyncio.Lock:
        """Session-lane lock — serializes desktop runs on this host."""
        return self._desktop_lane.run_lane

    def desktop_active_run_id(self) -> str | None:
        """Return the run id currently holding the desktop lane, if any."""
        return self._desktop_lane.active_run_id()

    def set_desktop_active_run(self, run_id: str | None) -> None:
        """Mark (or clear) the run id that currently owns the desktop lane."""
        self._desktop_lane.set_active_run(run_id)

    @property
    def usage(self) -> UsageLedger:
        return self._usage

    @property
    def workspaces(self) -> WorkspaceManager:
        return self._workspaces

    @property
    def agent_registry(self) -> AgentRegistry:
        return self._agent_registry

    @property
    def custom_agents(self) -> CustomAgentRegistry:
        return self._custom_agents

    @property
    def inbound_dispatcher(self) -> InboundDispatcher:
        return self._inbound

    @property
    def dm_policy(self) -> DMPairingPolicy:
        return self._dm_policy

    @property
    def slash_state(self) -> SlashRuntimeState:
        return self._slash_state

    def get_flow_handlers(self) -> dict[str, Any]:
        """Bind intent names to flow handler coroutines."""
        return {
            "baselithbot_browse": self._flow_handler.handle_browse,
        }


__all__ = ["BaselithbotPlugin"]
