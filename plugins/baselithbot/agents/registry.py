"""Registry of co-located agents available to the Baselithbot plugin."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

AgentInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentEntry(BaseModel):
    name: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    priority: int = 100
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRegistry:
    """Hold ``AgentEntry`` + invoker callables keyed by agent name."""

    def __init__(self) -> None:
        self._entries: dict[str, AgentEntry] = {}
        self._invokers: dict[str, AgentInvoker] = {}

    def register(self, entry: AgentEntry, invoker: AgentInvoker) -> None:
        self._entries[entry.name] = entry
        self._invokers[entry.name] = invoker

    def remove(self, name: str) -> bool:
        existed = name in self._entries
        self._entries.pop(name, None)
        self._invokers.pop(name, None)
        return existed

    def get(self, name: str) -> AgentEntry | None:
        return self._entries.get(name)

    def list(self) -> list[AgentEntry]:
        return [e for e in self._entries.values()]

    async def invoke(self, name: str, query: str, context: dict[str, Any]) -> dict[str, Any]:
        invoker = self._invokers.get(name)
        if invoker is None:
            return {"status": "error", "error": f"agent '{name}' not registered"}
        return await invoker(query, context)


__all__ = ["AgentEntry", "AgentRegistry", "AgentInvoker"]
