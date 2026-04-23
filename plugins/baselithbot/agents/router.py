"""Keyword-based router across registered Baselithbot agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .registry import AgentEntry, AgentRegistry


class RoutingDecision(BaseModel):
    agent: str | None
    score: int
    reason: str


class AgentRouter:
    """Score agents by keyword overlap and pick the highest priority match."""

    def __init__(self, registry: AgentRegistry, default_agent: str | None = None) -> None:
        self._registry = registry
        self._default = default_agent

    def decide(self, query: str) -> RoutingDecision:
        normalized = query.lower()
        best: tuple[int, AgentEntry] | None = None
        for entry in self._registry.list():
            hits = sum(1 for kw in entry.keywords if kw.lower() in normalized)
            if hits == 0:
                continue
            score = hits * 100 + entry.priority
            if best is None or score > best[0]:
                best = (score, entry)
        if best is None:
            if self._default and self._registry.get(self._default):
                return RoutingDecision(agent=self._default, score=0, reason="default fallback")
            return RoutingDecision(agent=None, score=0, reason="no keyword match")
        return RoutingDecision(
            agent=best[1].name,
            score=best[0],
            reason=f"keyword match score={best[0]}",
        )

    async def dispatch(self, query: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        decision = self.decide(query)
        if decision.agent is None:
            return {"status": "no_match", "decision": decision.model_dump()}
        out = await self._registry.invoke(decision.agent, query, context or {})
        return {
            "status": "dispatched",
            "decision": decision.model_dump(),
            "result": out,
        }


__all__ = ["AgentRouter", "RoutingDecision"]
