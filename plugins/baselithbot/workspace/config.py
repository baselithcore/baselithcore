"""Workspace state isolation (sessions / channels / skills bound to a name)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..channels import ChannelRegistry, build_default_registry
from ..sessions import SessionManager
from ..skills import SkillRegistry


class WorkspaceNotFoundError(KeyError):
    """Raised when a workspace name is missing from the manager."""


class WorkspaceConfig(BaseModel):
    name: str
    description: str = ""
    primary: bool = False
    channel_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Workspace(BaseModel):
    config: WorkspaceConfig
    created_at: float = Field(default_factory=time.time)

    model_config = {"arbitrary_types_allowed": True}

    def runtime_summary(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "primary": self.config.primary,
            "created_at": self.created_at,
            "channels_overridden": list(self.config.channel_overrides.keys()),
        }


class WorkspaceManager:
    """Hold one isolated state graph per workspace."""

    def __init__(self) -> None:
        self._workspaces: dict[str, Workspace] = {}
        self._channels: dict[str, ChannelRegistry] = {}
        self._sessions: dict[str, SessionManager] = {}
        self._skills: dict[str, SkillRegistry] = {}

    def create(self, config: WorkspaceConfig) -> Workspace:
        if config.name in self._workspaces:
            raise ValueError(f"workspace '{config.name}' already exists")
        ws = Workspace(config=config)
        self._workspaces[config.name] = ws
        self._channels[config.name] = build_default_registry()
        self._sessions[config.name] = SessionManager()
        self._skills[config.name] = SkillRegistry()
        return ws

    def get(self, name: str) -> Workspace:
        if name not in self._workspaces:
            raise WorkspaceNotFoundError(name)
        return self._workspaces[name]

    def list(self) -> list[Workspace]:
        return [w for w in self._workspaces.values()]

    def channels(self, name: str) -> ChannelRegistry:
        if name not in self._channels:
            raise WorkspaceNotFoundError(name)
        return self._channels[name]

    def sessions(self, name: str) -> SessionManager:
        if name not in self._sessions:
            raise WorkspaceNotFoundError(name)
        return self._sessions[name]

    def skills(self, name: str) -> SkillRegistry:
        if name not in self._skills:
            raise WorkspaceNotFoundError(name)
        return self._skills[name]

    def remove(self, name: str) -> bool:
        existed = name in self._workspaces
        self._workspaces.pop(name, None)
        self._channels.pop(name, None)
        self._sessions.pop(name, None)
        self._skills.pop(name, None)
        return existed

    @classmethod
    def from_json_file(cls, path: str | Path) -> "WorkspaceManager":
        manager = cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for raw in data.get("workspaces", []):
            manager.create(WorkspaceConfig.model_validate(raw))
        return manager


__all__ = [
    "Workspace",
    "WorkspaceConfig",
    "WorkspaceManager",
    "WorkspaceNotFoundError",
]
