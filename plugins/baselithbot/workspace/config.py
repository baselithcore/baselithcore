"""Workspace state isolation (sessions / channels / skills bound to a name)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..channels import ChannelRegistry, build_default_registry
from ..sessions import SessionManager
from ..skills import SkillRegistry

logger = logging.getLogger(__name__)


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
            "description": self.config.description,
            "primary": self.config.primary,
            "created_at": self.created_at,
            "channels_overridden": list(self.config.channel_overrides.keys()),
            "metadata": dict(self.config.metadata),
        }


class WorkspaceStore:
    """JSON-backed persistence for workspace configs."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[tuple[WorkspaceConfig, float]]:
        if not self._path.is_file():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("baselithbot_workspaces_load_failed: %s", exc)
            return []
        entries: list[tuple[WorkspaceConfig, float]] = []
        for item in raw.get("workspaces", []) if isinstance(raw, dict) else []:
            try:
                created = float(item.get("created_at", time.time()))
                cfg = WorkspaceConfig.model_validate(
                    {k: v for k, v in item.items() if k != "created_at"}
                )
                entries.append((cfg, created))
            except Exception as exc:
                logger.warning("baselithbot_workspaces_invalid_entry: %s -> %s", item, exc)
        return entries

    def save(self, workspaces: list[Workspace]) -> None:
        payload = {
            "workspaces": [
                {**ws.config.model_dump(mode="json"), "created_at": ws.created_at}
                for ws in workspaces
            ]
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)


class WorkspaceManager:
    """Hold one isolated state graph per workspace."""

    def __init__(self, store: WorkspaceStore | None = None) -> None:
        self._workspaces: dict[str, Workspace] = {}
        self._channels: dict[str, ChannelRegistry] = {}
        self._sessions: dict[str, SessionManager] = {}
        self._skills: dict[str, SkillRegistry] = {}
        self._store = store

    def bootstrap(self) -> int:
        if self._store is None:
            return 0
        loaded = 0
        for cfg, created_at in self._store.load():
            if cfg.name in self._workspaces:
                continue
            self._insert(Workspace(config=cfg, created_at=created_at))
            loaded += 1
        return loaded

    def _insert(self, workspace: Workspace) -> Workspace:
        name = workspace.config.name
        self._workspaces[name] = workspace
        self._channels[name] = build_default_registry()
        self._sessions[name] = SessionManager()
        self._skills[name] = SkillRegistry()
        return workspace

    def _persist(self) -> None:
        if self._store is not None:
            self._store.save(list(self._workspaces.values()))

    def create(self, config: WorkspaceConfig) -> Workspace:
        if config.name in self._workspaces:
            raise ValueError(f"workspace '{config.name}' already exists")
        if config.primary:
            self._demote_primaries()
        ws = self._insert(Workspace(config=config))
        self._persist()
        return ws

    def update(self, name: str, config: WorkspaceConfig) -> Workspace:
        if name not in self._workspaces:
            raise WorkspaceNotFoundError(name)
        if config.name != name:
            raise ValueError("workspace name is immutable")
        if config.primary:
            self._demote_primaries(except_name=name)
        existing = self._workspaces[name]
        updated = Workspace(config=config, created_at=existing.created_at)
        self._workspaces[name] = updated
        self._persist()
        return updated

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
        if existed:
            self._persist()
        return existed

    def _demote_primaries(self, except_name: str | None = None) -> None:
        for ws_name, ws in self._workspaces.items():
            if ws_name == except_name:
                continue
            if ws.config.primary:
                ws.config.primary = False

    @classmethod
    def from_json_file(cls, path: str | Path) -> WorkspaceManager:
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
    "WorkspaceStore",
]
