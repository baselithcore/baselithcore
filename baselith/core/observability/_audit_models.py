"""Query and result models of the SQLite audit sink.

Split out of :mod:`baselith.core.observability.audit_chain`, which re-exports them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from baselith.core.observability.audit_digest import GENESIS_HASH


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


#: Most rows one :meth:`SQLiteAuditSink.query` call returns. An exporter pages
#: with ``after_seq``; nothing needs the whole trail in one list.
MAX_QUERY_LIMIT = 10_000


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
    #: Only records whose chain sequence number is greater than this (``0`` is
    #: everything). The cursor an exporter keeps between polls: ``seq`` is monotonic,
    #: unique and survives a retention purge, which removes a prefix, never a middle link.
    after_seq: int | None = None
    #: Oldest first (``seq`` ascending), the order an exporter reads. The default is
    #: newest first, which suits a screen.
    ascending: bool = False

    def page(self) -> tuple[int, int]:
        """``(limit, offset)`` as SQLite may run them.

        SQLite reads a negative ``LIMIT`` as *no limit*, so an unchecked
        ``limit=-1`` returned the whole trail into memory. The limit is held to
        ``0..MAX_QUERY_LIMIT`` and the offset to zero or more.
        """
        return max(0, min(int(self.limit), MAX_QUERY_LIMIT)), max(0, int(self.offset))

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
        if self.after_seq is not None:
            clauses.append("seq > ?")
            params.append(int(self.after_seq))
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


@dataclass(slots=True)
class _ChainedRow:
    """A materialised audit row with its chain metadata."""

    seq: int
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str
    columns: dict[str, Any] = field(default_factory=dict)


__all__ = ["MAX_QUERY_LIMIT", "AuditQuery", "ChainVerification", "_ChainedRow"]
