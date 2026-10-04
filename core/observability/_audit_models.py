"""Query and result models of the SQLite audit sink.

Split out of :mod:`core.observability.audit_chain`, which re-exports them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.observability.audit_digest import GENESIS_HASH


@dataclass(slots=True)
class ChainVerification:
    """Outcome of a :meth:`SQLiteAuditSink.verify_chain` pass."""

    ok: bool
    checked: int
    broken_at: int | None = None
    reason: str | None = None
    anchor_hash: str = GENESIS_HASH
    head_hash: str = GENESIS_HASH

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "broken_at": self.broken_at,
            "reason": self.reason,
            "anchor_hash": self.anchor_hash,
            "head_hash": self.head_hash,
        }


@dataclass(slots=True)
class AuditQuery:
    """Filter for :meth:`SQLiteAuditSink.query`. Unset fields are ignored."""

    event_type: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 100
    offset: int = 0

    def where(self) -> tuple[str, list[Any]]:
        """Render the filter as a SQL ``WHERE`` fragment plus its parameters."""
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("event_type", self.event_type),
            ("user_id", self.user_id),
            ("tenant_id", self.tenant_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if self.since is not None:
            clauses.append("timestamp >= ?")
            params.append(self.since.astimezone(UTC).isoformat())
        if self.until is not None:
            clauses.append("timestamp <= ?")
            params.append(self.until.astimezone(UTC).isoformat())
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


@dataclass(slots=True)
class _ChainedRow:
    """A materialised audit row with its chain metadata."""

    seq: int
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str
    columns: dict[str, Any] = field(default_factory=dict)


__all__ = ["AuditQuery", "ChainVerification", "_ChainedRow"]
