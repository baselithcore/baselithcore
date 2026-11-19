"""The data-subject request register — GDPR Art. 12(3).

``DataSubjectService`` *carries out* the Chapter III rights (export, erasure,
rectification, restriction, objection). What it does not keep is the question a
supervisory authority opens with: **was each request answered within a month?**
Art. 12(3) requires the controller to provide information on action taken
"without undue delay and in any event within one month of receipt of the
request", extendable by two further months where necessary given the complexity
and number of requests, **the data subject being told of the extension within
one month of receipt, together with the reasons**. Art. 12(4): when the
controller does not act, it informs the data subject within the same month of
the reasons and of the right to complain and to seek a judicial remedy.

A request that arrives by e-mail on a Friday has no record anywhere until
somebody makes one; this is that record, with the clock.

**Conservative by design.**

* The month is a **calendar month** (31 January → 28 February), not 30 days; see
  :func:`baselith.core.incidents.types.one_month_after`.
* The clock runs **from receipt**. Asking the person to prove who they are
  (Art. 12(6)) is recorded, but does **not** stop it: a register that paused the
  clock on its own reading of the law would show a later deadline than the
  strictest one. A DPO who relies on suspension can read the recorded events.
* An extension counts only if the data subject was told **within the first
  month**: ``extension_valid`` says so, and an extension noted late does not
  move the deadline.

The register holds an opaque ``subject_ref`` (the identifier the providers use)
and metadata. It never holds the request's content, and the ``title`` it exposes
to dashboards, logs and the event bus names the *kind* of request and a short
id, never the person.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from baselith.core.incidents.types import (
    DsrMilestoneKind,
    ReportingMilestone,
    add_calendar_months,
    one_month_after,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class DsrRequestType(str, Enum):
    """Which right the person invoked (GDPR Arts. 15-22)."""

    ACCESS = "access"
    RECTIFICATION = "rectification"
    ERASURE = "erasure"
    RESTRICTION = "restriction"
    PORTABILITY = "portability"
    OBJECTION = "objection"
    OTHER = "other"


class DsrRequestStatus(str, Enum):
    RECEIVED = "received"
    IDENTITY_PENDING = "identity_pending"
    IN_PROGRESS = "in_progress"
    FULFILLED = "fulfilled"
    REFUSED = "refused"


#: Statuses after which the request is answered.
CLOSED_STATUSES = frozenset({DsrRequestStatus.FULFILLED, DsrRequestStatus.REFUSED})

#: The extension Art. 12(3) allows: two further months.
EXTENSION_MONTHS = 2


class DsrRequestError(ValueError):
    """The step is not allowed in the request's current state."""


class DsrRequestNotFoundError(LookupError):
    """No request with that id."""


@dataclass
class DsrRequest:
    """One request, from receipt to answer."""

    request_type: DsrRequestType
    subject_ref: str
    received_at: datetime = field(default_factory=_utcnow)
    #: How it arrived: ``email``, ``post``, ``portal``, ``phone`` — free text.
    channel: str = ""
    status: DsrRequestStatus = DsrRequestStatus.RECEIVED
    identity_requested_at: datetime | None = None
    identity_confirmed_at: datetime | None = None
    extended: bool = False
    extension_reason: str = ""
    extension_notified_at: datetime | None = None
    answered_at: datetime | None = None
    refusal_reason: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @property
    def title(self) -> str:
        """What dashboards, logs and events show: the kind, never the person."""
        return f"{self.request_type.value} request {self.id[:8]}"

    @property
    def base_due_at(self) -> datetime:
        """One calendar month from receipt: the deadline without an extension."""
        return one_month_after(self.received_at)

    @property
    def extension_valid(self) -> bool:
        """Whether the extension counts: the person was told within the month."""
        return (
            self.extended
            and self.extension_notified_at is not None
            and self.extension_notified_at <= self.base_due_at
        )

    @property
    def due_at(self) -> datetime:
        """The governing deadline: the month, or three months if validly extended."""
        if self.extension_valid:
            return add_calendar_months(self.received_at, 1 + EXTENSION_MONTHS)
        return self.base_due_at

    @property
    def is_late(self) -> bool:
        """Whether the answer went out after the governing deadline."""
        return self.answered_at is not None and self.answered_at > self.due_at

    def milestones(self) -> list[ReportingMilestone]:
        return [
            ReportingMilestone(DsrMilestoneKind.RESPONSE, self.due_at, self.answered_at)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "request_type": self.request_type.value,
            "subject_ref": self.subject_ref,
            "received_at": self.received_at.isoformat(),
            "channel": self.channel,
            "status": self.status.value,
            "identity_requested_at": _iso(self.identity_requested_at),
            "identity_confirmed_at": _iso(self.identity_confirmed_at),
            "extended": self.extended,
            "extension_reason": self.extension_reason,
            "extension_notified_at": _iso(self.extension_notified_at),
            "extension_valid": self.extension_valid,
            "base_due_at": self.base_due_at.isoformat(),
            "due_at": self.due_at.isoformat(),
            "answered_at": _iso(self.answered_at),
            "is_late": self.is_late,
            "refusal_reason": self.refusal_reason,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DsrRequest:
        return cls(
            request_type=DsrRequestType(data["request_type"]),
            subject_ref=data["subject_ref"],
            received_at=datetime.fromisoformat(data["received_at"]),
            channel=data.get("channel", ""),
            status=DsrRequestStatus(data["status"]),
            identity_requested_at=_dt(data.get("identity_requested_at")),
            identity_confirmed_at=_dt(data.get("identity_confirmed_at")),
            extended=data.get("extended", False),
            extension_reason=data.get("extension_reason", ""),
            extension_notified_at=_dt(data.get("extension_notified_at")),
            answered_at=_dt(data.get("answered_at")),
            refusal_reason=data.get("refusal_reason", ""),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


class DsrRequestStore(Protocol):
    async def save(self, request: DsrRequest) -> None: ...
    async def get(self, request_id: str) -> DsrRequest | None: ...
    async def list_all(self) -> list[DsrRequest]: ...


class InMemoryDsrRequestStore:
    """Reference store (non-durable)."""

    def __init__(self) -> None:
        self._rows: dict[str, DsrRequest] = {}

    async def save(self, request: DsrRequest) -> None:
        self._rows[request.id] = request

    async def get(self, request_id: str) -> DsrRequest | None:
        return self._rows.get(request_id)

    async def list_all(self) -> list[DsrRequest]:
        return sorted(self._rows.values(), key=lambda r: r.received_at)


class SQLiteDsrRequestStore:
    """Durable store: one JSON row per request, one ``to_thread`` hop per call."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS dsr_requests "
            "(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self._lock = RLock()

    def _upsert(self, request: DsrRequest) -> None:
        blob = json.dumps(request.to_dict(), sort_keys=True, default=str)
        with self._lock:
            self._conn.execute(
                "INSERT INTO dsr_requests (id, data) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (request.id, blob),
            )

    def _fetch(self, request_id: str) -> DsrRequest | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM dsr_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return DsrRequest.from_dict(json.loads(row[0])) if row else None

    def _all(self) -> list[DsrRequest]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM dsr_requests").fetchall()
        found = [DsrRequest.from_dict(json.loads(r[0])) for r in rows]
        return sorted(found, key=lambda r: r.received_at)

    async def save(self, request: DsrRequest) -> None:
        await asyncio.to_thread(self._upsert, request)

    async def get(self, request_id: str) -> DsrRequest | None:
        return await asyncio.to_thread(self._fetch, request_id)

    async def list_all(self) -> list[DsrRequest]:
        return await asyncio.to_thread(self._all)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # silent-ok: SQLite teardown
                pass


__all__ = [
    "CLOSED_STATUSES",
    "EXTENSION_MONTHS",
    "DsrRequest",
    "DsrRequestError",
    "DsrRequestNotFoundError",
    "DsrRequestStatus",
    "DsrRequestStore",
    "DsrRequestType",
    "InMemoryDsrRequestStore",
    "SQLiteDsrRequestStore",
]
