"""In-memory session manager with bounded history per session."""

from __future__ import annotations

import builtins
import time
import uuid
from collections import OrderedDict, deque
from typing import Any

from pydantic import BaseModel, Field


class SessionMessage(BaseModel):
    role: str
    content: str
    ts: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    id: str
    title: str = ""
    created_at: float = Field(default_factory=time.time)
    last_active: float = Field(default_factory=time.time)
    primary: bool = False
    sandbox: dict[str, Any] | None = None


class SessionManager:
    """Maintain ordered sessions with bounded message history."""

    def __init__(self, history_limit: int = 200, max_sessions: int = 64) -> None:
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._history: dict[str, deque[SessionMessage]] = {}
        self._history_limit = history_limit
        self._max_sessions = max_sessions

    def create(self, title: str = "", primary: bool = False) -> Session:
        sid = uuid.uuid4().hex[:12]
        session = Session(id=sid, title=title, primary=primary)
        self._sessions[sid] = session
        self._history[sid] = deque(maxlen=self._history_limit)
        if len(self._sessions) > self._max_sessions:
            oldest, _ = self._sessions.popitem(last=False)
            self._history.pop(oldest, None)
        return session

    def list(self) -> builtins.list[Session]:
        return [s for s in self._sessions.values()]

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)

    def history(self, sid: str, limit: int = 50) -> builtins.list[SessionMessage]:
        history = self._history.get(sid)
        if history is None:
            return []
        return [m for m in history][-limit:]

    def send(self, sid: str, message: SessionMessage) -> SessionMessage:
        if sid not in self._sessions:
            raise KeyError(f"session '{sid}' not found")
        self._history[sid].append(message)
        self._sessions[sid].last_active = time.time()
        return message

    def reset(self, sid: str) -> None:
        if sid in self._history:
            self._history[sid].clear()

    def delete(self, sid: str) -> bool:
        existed = sid in self._sessions
        self._sessions.pop(sid, None)
        self._history.pop(sid, None)
        return existed

    def prune_inactive(self, ttl_seconds: float) -> int:
        """Drop non-primary sessions idle longer than ``ttl_seconds``."""
        cutoff = time.time() - ttl_seconds
        stale = [
            sid
            for sid, session in self._sessions.items()
            if not session.primary and session.last_active < cutoff
        ]
        for sid in stale:
            self._sessions.pop(sid, None)
            self._history.pop(sid, None)
        return len(stale)


__all__ = ["Session", "SessionManager", "SessionMessage"]
