"""Human-in-the-loop approval gate for privileged Computer Use actions.

When ``ComputerUseConfig.require_approval_for`` lists a capability, every
attempted action of that capability is suspended in a pending state until
an operator approves or denies it via the dashboard (or a
``timeout_seconds`` window elapses, which defaults to *deny*).

The gate is asyncio-native:
    - ``submit`` creates an :class:`ApprovalRequest`, parks the caller on an
      ``asyncio.Event``, and returns the *final* decision
      (approved / denied / timed_out).
    - ``approve`` / ``deny`` resolve pending requests and release the caller.
    - ``pending`` / ``snapshot`` power the dashboard list view.

Requests live in memory only — they are tied to a single agent run.
Persisted audit happens via the existing ``AuditLogger`` once the action
either executes or is refused.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


@dataclass
class ApprovalRequest:
    """A single privileged-action approval request awaiting operator input."""

    id: str
    capability: str
    action: str
    params: dict[str, Any]
    submitted_at: float
    timeout_seconds: float
    status: ApprovalStatus = ApprovalStatus.PENDING
    resolved_at: float | None = None
    reason: str | None = None
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for the dashboard."""
        return {
            "id": self.id,
            "capability": self.capability,
            "action": self.action,
            "params": self.params,
            "submitted_at": self.submitted_at,
            "timeout_seconds": self.timeout_seconds,
            "status": self.status.value,
            "resolved_at": self.resolved_at,
            "reason": self.reason,
            "expires_at": self.submitted_at + self.timeout_seconds,
        }


class ApprovalGate:
    """Thread-safe registry of pending + resolved approval requests."""

    def __init__(self, history_size: int = 200) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalRequest] = []
        self._history_size = max(10, history_size)
        self._lock = asyncio.Lock()
        self._listeners: list[asyncio.Queue[dict[str, Any]]] = []

    async def submit(
        self,
        *,
        capability: str,
        action: str,
        params: dict[str, Any],
        timeout_seconds: float = 120.0,
    ) -> ApprovalRequest:
        """Park the caller until the request is resolved or times out.

        Returns the resolved :class:`ApprovalRequest` — callers inspect
        ``status`` to decide whether to proceed.
        """
        req = ApprovalRequest(
            id=uuid.uuid4().hex,
            capability=capability,
            action=action,
            params=params,
            submitted_at=time.time(),
            timeout_seconds=max(1.0, float(timeout_seconds)),
        )
        async with self._lock:
            self._pending[req.id] = req
        logger.info(
            "baselithbot_approval_submitted",
            id=req.id,
            capability=capability,
            action=action,
        )
        self._broadcast("approval.pending", req.snapshot())

        try:
            await asyncio.wait_for(req._event.wait(), timeout=req.timeout_seconds)
        except TimeoutError:
            async with self._lock:
                if req.status == ApprovalStatus.PENDING:
                    req.status = ApprovalStatus.TIMED_OUT
                    req.resolved_at = time.time()
                    req.reason = "timeout"
                self._move_to_history_locked(req)
            logger.info("baselithbot_approval_timed_out", id=req.id)
            self._broadcast("approval.resolved", req.snapshot())
        return req

    async def approve(self, request_id: str, *, reason: str | None = None) -> bool:
        return await self._resolve(request_id, ApprovalStatus.APPROVED, reason)

    async def deny(self, request_id: str, *, reason: str | None = None) -> bool:
        return await self._resolve(request_id, ApprovalStatus.DENIED, reason)

    async def _resolve(
        self,
        request_id: str,
        status: ApprovalStatus,
        reason: str | None,
    ) -> bool:
        async with self._lock:
            req = self._pending.get(request_id)
            if req is None or req.status != ApprovalStatus.PENDING:
                return False
            req.status = status
            req.resolved_at = time.time()
            req.reason = reason
            req._event.set()
            self._move_to_history_locked(req)
        logger.info(
            "baselithbot_approval_resolved",
            id=request_id,
            status=status.value,
            reason=reason,
        )
        self._broadcast("approval.resolved", req.snapshot())
        return True

    def _move_to_history_locked(self, req: ApprovalRequest) -> None:
        self._pending.pop(req.id, None)
        self._history.append(req)
        if len(self._history) > self._history_size:
            self._history = self._history[-self._history_size :]

    async def pending(self) -> list[ApprovalRequest]:
        async with self._lock:
            return list(self._pending.values())

    async def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        async with self._lock:
            return {
                "pending": [r.snapshot() for r in self._pending.values()],
                "history": [r.snapshot() for r in self._history[-50:]],
            }

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Return a broadcast queue populated on every state change."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._listeners.remove(queue)
        except ValueError:
            pass

    def _broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._listeners:
            try:
                queue.put_nowait({"type": event_type, "payload": payload})
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self.unsubscribe(queue)


__all__ = ["ApprovalGate", "ApprovalRequest", "ApprovalStatus"]
