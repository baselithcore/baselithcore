"""Usage tracking ledger (token / cost / latency per session / agent / channel).

Append-only ledger persisted as JSON Lines plus an in-memory ring buffer.
Aggregations expose totals per session, agent, channel, and model.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger
from pydantic import BaseModel, Field

logger = get_logger(__name__)


class UsageEvent(BaseModel):
    ts: float = Field(default_factory=time.time)
    session_id: str | None = None
    agent_id: str | None = None
    channel: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageLedger:
    """Append-only ledger with summarization helpers."""

    def __init__(
        self,
        ledger_path: str | None = None,
        ring_buffer_size: int = 5000,
    ) -> None:
        self._ring: deque[UsageEvent] = deque(maxlen=ring_buffer_size)
        self._path = Path(ledger_path) if ledger_path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: UsageEvent) -> None:
        if event.total_tokens == 0 and (event.prompt_tokens or event.completion_tokens):
            event.total_tokens = event.prompt_tokens + event.completion_tokens
        self._ring.append(event)
        logger.info(
            "baselithbot_usage_event",
            session_id=event.session_id,
            agent_id=event.agent_id,
            channel=event.channel,
            model=event.model,
            tokens=event.total_tokens,
            cost_usd=event.cost_usd,
        )
        if self._path is None:
            return
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")

    def recent(self, limit: int = 100) -> list[UsageEvent]:
        return list(self._ring)[-limit:]

    def summary(self) -> dict[str, Any]:
        total_tokens = 0
        total_cost = 0.0
        latencies: list[float] = []
        for ev in self._ring:
            total_tokens += ev.total_tokens
            total_cost += ev.cost_usd
            if ev.latency_ms:
                latencies.append(ev.latency_ms)
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        return {
            "events_in_buffer": len(self._ring),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "avg_latency_ms": round(avg_latency, 2),
        }

    def by_session(self, session_id: str) -> dict[str, Any]:
        return self._aggregate(lambda ev: ev.session_id == session_id)

    def by_agent(self, agent_id: str) -> dict[str, Any]:
        return self._aggregate(lambda ev: ev.agent_id == agent_id)

    def by_channel(self, channel: str) -> dict[str, Any]:
        return self._aggregate(lambda ev: ev.channel == channel)

    def by_model_breakdown(self) -> dict[str, dict[str, Any]]:
        models: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"events": 0, "tokens": 0, "cost_usd": 0.0}
        )
        for ev in self._ring:
            key = ev.model or "unknown"
            models[key]["events"] += 1
            models[key]["tokens"] += ev.total_tokens
            models[key]["cost_usd"] = round(models[key]["cost_usd"] + ev.cost_usd, 6)
        return dict(models)

    def _aggregate(self, predicate: Any) -> dict[str, Any]:
        events = [ev for ev in self._ring if predicate(ev)]
        return {
            "events": len(events),
            "tokens": sum(e.total_tokens for e in events),
            "cost_usd": round(sum(e.cost_usd for e in events), 6),
        }

    def replay(self, limit: int | None = None) -> list[UsageEvent]:
        if self._path is None or not self._path.is_file():
            return []
        events: list[UsageEvent] = []
        with self._path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(UsageEvent.model_validate_json(line))
                except (ValueError, json.JSONDecodeError):
                    continue
                if limit and len(events) >= limit:
                    break
        return events


__all__ = ["UsageEvent", "UsageLedger"]
