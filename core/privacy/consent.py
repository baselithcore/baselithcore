"""GDPR Art. 7 consent records.

Where consent is the lawful basis, Art. 7(1) puts the burden of proof on the
controller: it must be able to **demonstrate** that the data subject consented.
A boolean column set at signup does not demonstrate anything — what does is a
record of *what* was consented to, *when*, against *which version* of the notice,
and *how* the consent was captured.

The other half is Art. 7(3): withdrawal must be as easy as giving consent, and
takes effect for the future without affecting prior lawfulness. Withdrawal here
is therefore a new state on the same record chain, not a deletion — deleting the
grant would destroy the evidence that processing before the withdrawal was
lawful.

Consent records are themselves personal data, so :class:`ConsentService`
implements the :class:`~core.privacy.provider.DataProvider` protocol and can be
registered alongside every other store.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ConsentRecord:
    """One consent decision for one subject and one purpose."""

    subject_id: str
    purpose: str
    granted: bool = True
    #: Version of the privacy notice / consent text the subject saw — Art. 7(2)
    #: requires the request to be intelligible and clearly distinguishable, and
    #: a changed notice needs fresh consent.
    notice_version: str = ""
    #: How the consent was captured (form id, UI surface, API client…).
    evidence: str = ""
    granted_at: float = field(default_factory=time.time)
    withdrawn_at: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def is_active(self) -> bool:
        """Whether this consent is currently in force."""
        return self.granted and self.withdrawn_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "purpose": self.purpose,
            "granted": self.granted,
            "notice_version": self.notice_version,
            "evidence": self.evidence,
            "granted_at": self.granted_at,
            "withdrawn_at": self.withdrawn_at,
            "is_active": self.is_active,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsentRecord:
        """Reconstruct a record from its :meth:`to_dict` payload (round-trip)."""
        return cls(
            subject_id=data["subject_id"],
            purpose=data["purpose"],
            granted=data.get("granted", True),
            notice_version=data.get("notice_version", ""),
            evidence=data.get("evidence", ""),
            granted_at=data.get("granted_at", 0.0),
            withdrawn_at=data.get("withdrawn_at"),
            details=dict(data.get("details", {})),
            id=data["id"],
        )


class ConsentStore(Protocol):
    """Persistence boundary for consent records."""

    async def append(self, record: ConsentRecord) -> None:
        """Append a record (the log is append-only — see Art. 7(3))."""
        ...

    async def for_subject(self, subject_id: str) -> list[ConsentRecord]:
        """Every record held for a subject, oldest first."""
        ...

    async def drop_subject(self, subject_id: str) -> int:
        """Remove every record for a subject; returns how many were removed."""
        ...


class InMemoryConsentStore:
    """Reference in-memory store (non-durable; tests/single-process)."""

    def __init__(self) -> None:
        self._records: dict[str, list[ConsentRecord]] = {}

    async def append(self, record: ConsentRecord) -> None:
        self._records.setdefault(record.subject_id, []).append(record)

    async def for_subject(self, subject_id: str) -> list[ConsentRecord]:
        return list(self._records.get(subject_id, []))

    async def drop_subject(self, subject_id: str) -> int:
        return len(self._records.pop(subject_id, []))


class SQLiteConsentStore:
    """Durable, append-only consent log over stdlib :mod:`sqlite3`.

    In-memory consent proof is not proof: it disappears on restart, and Art. 7(1)
    asks the controller to demonstrate consent *later*, when challenged. This
    store is what makes the record chain outlive the process.

    Rows are ordered by an autoincrementing sequence rather than by timestamp,
    so a grant and a withdrawal recorded inside the same clock tick still read
    back in the order they happened — which is exactly the pair whose order
    decides whether consent is currently in force.

    ``check_same_thread=False`` plus the internal :class:`~threading.RLock` makes
    the single connection safe to share across threads, so each statement runs on
    a worker thread via :func:`asyncio.to_thread` instead of blocking the event
    loop on disk I/O. One hop covers the whole unit of work — lock, statement,
    fetch, JSON decode and record rehydration.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS consent_log (
        seq        INTEGER PRIMARY KEY AUTOINCREMENT,
        id         TEXT NOT NULL UNIQUE,
        subject_id TEXT NOT NULL,
        purpose    TEXT NOT NULL,
        data       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_consent_subject ON consent_log (subject_id);
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(self._SCHEMA)
        self._lock = RLock()

    # -- Blocking units of work (run on a worker thread) -------------------

    def _append_sync(self, record: ConsentRecord) -> None:
        blob = json.dumps(record.to_dict(), sort_keys=True, default=str)
        with self._lock:
            self._conn.execute(
                "INSERT INTO consent_log (id, subject_id, purpose, data) "
                "VALUES (?, ?, ?, ?)",
                (record.id, record.subject_id, record.purpose, blob),
            )

    def _for_subject_sync(self, subject_id: str) -> list[ConsentRecord]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT data FROM consent_log WHERE subject_id = ? ORDER BY seq ASC",
                (subject_id,),
            )
            rows = cur.fetchall()
        return [ConsentRecord.from_dict(json.loads(r[0])) for r in rows]

    def _drop_subject_sync(self, subject_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM consent_log WHERE subject_id = ?", (subject_id,)
            )
            return max(cur.rowcount, 0)

    # -- Async surface: exactly one ``to_thread`` hop per operation ---------

    async def append(self, record: ConsentRecord) -> None:
        await asyncio.to_thread(self._append_sync, record)

    async def for_subject(self, subject_id: str) -> list[ConsentRecord]:
        return await asyncio.to_thread(self._for_subject_sync, subject_id)

    async def drop_subject(self, subject_id: str) -> int:
        return await asyncio.to_thread(self._drop_subject_sync, subject_id)

    def close(self) -> None:
        """Close the underlying connection. Never raises."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # close must never raise
                pass


class ConsentService:
    """Grant, withdraw and prove consent — and expose it as a data provider."""

    def __init__(
        self, store: ConsentStore | None = None, *, name: str = "consent"
    ) -> None:
        self._store = store or InMemoryConsentStore()
        self._name = name

    @property
    def name(self) -> str:
        """Provider name, for the DSR registry."""
        return self._name

    async def grant(
        self,
        subject_id: str,
        purpose: str,
        *,
        notice_version: str = "",
        evidence: str = "",
        details: dict[str, Any] | None = None,
    ) -> ConsentRecord:
        """Record that a subject consented to ``purpose``."""
        record = ConsentRecord(
            subject_id=subject_id,
            purpose=purpose,
            granted=True,
            notice_version=notice_version,
            evidence=evidence,
            details=dict(details or {}),
        )
        await self._store.append(record)
        logger.info(
            "AUDIT | PRIVACY | consent granted | subject=%s purpose=%s notice=%s",
            subject_id,
            purpose,
            notice_version or "-",
        )
        await get_audit_logger().log(
            AuditEventType.PRIVACY_CONSENT,
            resource=subject_id,
            action="grant",
            details={
                "purpose": purpose,
                "notice_version": notice_version,
                "evidence": evidence,
            },
        )
        return record

    async def withdraw(self, subject_id: str, purpose: str) -> ConsentRecord | None:
        """Withdraw consent for ``purpose`` (Art. 7(3)).

        Appends a withdrawal record rather than deleting the grant: withdrawal
        operates for the future and must not call into question the lawfulness
        of processing that already happened. Returns ``None`` when there was no
        active consent to withdraw.
        """
        if not await self.has_consent(subject_id, purpose):
            return None
        record = ConsentRecord(
            subject_id=subject_id,
            purpose=purpose,
            granted=False,
            withdrawn_at=time.time(),
        )
        await self._store.append(record)
        logger.info(
            "AUDIT | PRIVACY | consent withdrawn | subject=%s purpose=%s",
            subject_id,
            purpose,
        )
        await get_audit_logger().log(
            AuditEventType.PRIVACY_CONSENT,
            resource=subject_id,
            action="withdraw",
            details={"purpose": purpose},
        )
        return record

    async def has_consent(self, subject_id: str, purpose: str) -> bool:
        """Whether consent for ``purpose`` is currently in force.

        The latest record for the purpose wins, so a re-grant after a withdrawal
        restores consent without rewriting history.
        """
        records = [
            r for r in await self._store.for_subject(subject_id) if r.purpose == purpose
        ]
        if not records:
            return False
        return records[-1].granted and records[-1].withdrawn_at is None

    async def active_purposes(self, subject_id: str) -> list[str]:
        """Every purpose the subject currently consents to."""
        latest: dict[str, ConsentRecord] = {}
        for record in await self._store.for_subject(subject_id):
            latest[record.purpose] = record
        return sorted(p for p, r in latest.items() if r.is_active)

    async def history(self, subject_id: str) -> list[ConsentRecord]:
        """The full record chain — the Art. 7(1) proof."""
        return await self._store.for_subject(subject_id)

    # -- DataProvider protocol ------------------------------------------------

    async def export(self, subject_id: str) -> list[dict[str, Any]]:
        """Export the subject's consent history (right to access)."""
        return [r.to_dict() for r in await self._store.for_subject(subject_id)]

    async def erase(self, subject_id: str) -> int:
        """Erase the subject's consent records.

        Consent records are personal data and fall under Art. 17 like anything
        else. Note the trade-off this makes explicit: erasing them also destroys
        the Art. 7(1) evidence that past processing was consented to. Where the
        controller needs that evidence for the establishment or defence of legal
        claims, Art. 17(3)(e) is the ground to retain it — implement a store
        that honours the exemption rather than quietly refusing here.
        """
        removed = await self._store.drop_subject(subject_id)
        logger.info(
            "AUDIT | PRIVACY | consent erased | subject=%s records=%d",
            subject_id,
            removed,
        )
        return removed


_service: ConsentService | None = None


def get_consent_service() -> ConsentService:
    """Get or create the global consent service.

    Uses the durable SQLite store when ``PRIVACY_CONSENT_DB_PATH`` is set, else
    the in-memory reference store (the default).
    """
    global _service
    if _service is None:
        from core.config.privacy import get_privacy_config

        path = get_privacy_config().consent_db_path
        _service = ConsentService(store=SQLiteConsentStore(path) if path else None)
    return _service


def reset_consent_service() -> None:
    """Drop the cached service (tests, and reconfiguration)."""
    global _service
    _service = None


__all__ = [
    "ConsentRecord",
    "ConsentService",
    "ConsentStore",
    "InMemoryConsentStore",
    "SQLiteConsentStore",
    "get_consent_service",
    "reset_consent_service",
]
