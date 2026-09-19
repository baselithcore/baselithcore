"""Durable, tamper-evident audit sink.

:mod:`core.observability.audit` defines the event model and the fan-out logger;
this module supplies the sink that makes an audit trail *evidence* rather than
telemetry: records survive restarts, stay queryable, and are hash-chained so a
deletion or an edit inside the retention window is detectable after the fact.

Regulatory anchor — the EU AI Act requires automatically generated logs
(Art. 12) to be **kept for at least six months** by the provider (Art. 19) and
the deployer (Art. 26(6)); NIS2 Art. 21(2)(b) expects an evidence trail behind
each incident filing; GDPR Art. 5(2) requires being able to *demonstrate*
compliance. A log line that only ever reaches stdout satisfies none of them.

Storage is stdlib :mod:`sqlite3`, matching :mod:`core.incidents.persistence`:
zero new dependencies, no infrastructure, and a single-writer model that suits
append-only workloads. ``check_same_thread=False`` plus an internal
:class:`~threading.RLock` makes the connection safe to share across the event
loop and worker threads; writes are offloaded to the default executor so a
disk-bound append never blocks the request path.

**Tamper evidence.** Each row stores ``prev_hash`` (the predecessor's
``entry_hash``) and ``entry_hash = MAC(prev_hash || canonical_json(event))``.
Editing or removing a record inside the chain breaks every downstream link, and
:meth:`SQLiteAuditSink.verify_chain` reports the first broken sequence number.

``MAC`` is **HMAC-SHA256 under ``AUDIT_CHAIN_HMAC_KEY``** when one is
configured, and a plain SHA-256 otherwise. The difference is the whole point of
the chain: an unkeyed digest is reproducible by anyone holding the file, so an
attacker with write access simply recomputes every link and the trail verifies
again — it detects corruption and accidents, not adversaries. With a key the
database does not contain, forgery needs the key too. Deployments where the
trail is evidence should set ``AUDIT_CHAIN_REQUIRE_KEY=true`` so an unkeyed
sink refuses to start.

*Rotating or first enabling the key does not re-key existing rows.* Records
written under a different key (or none) fail verification from then on —
verification is strict by design, because retrying unkeyed on failure would
hand an attacker exactly the downgrade they want. Rotate at a chain boundary:
archive the current database with its verification report, then start a new
one.

Even keyed, this is *detection*, not prevention. Anchor the digest externally
(ship :meth:`SQLiteAuditSink.head_hash` to a WORM store or a separate SIEM)
when the threat model includes a compromised host.

**Retention and truncation.** Purging by definition removes the chain's oldest
links. Verification therefore treats the earliest *surviving* row's stored
``prev_hash`` as a trusted anchor and validates forward from there, so a
retention sweep does not masquerade as tampering.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import SecretStr, ValidationError

from core.config.audit import get_audit_config
from core.observability.audit import AuditEvent
from core.observability.audit_digest import (
    GENESIS_HASH,
    AuditChainKeyError,
    coerce_chain_key,
    compute_entry_hash,
    require_chain_key_from_env,
)
from core.observability.logging import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT    NOT NULL UNIQUE,
    timestamp   TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    user_id     TEXT,
    tenant_id   TEXT,
    session_id  TEXT,
    resource    TEXT,
    action      TEXT,
    success     INTEGER NOT NULL,
    ip_address  TEXT,
    details     TEXT    NOT NULL,
    prev_hash   TEXT    NOT NULL,
    entry_hash  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp  ON audit_log (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log (event_type);
CREATE INDEX IF NOT EXISTS idx_audit_user       ON audit_log (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_tenant     ON audit_log (tenant_id);
"""

_COLUMNS = (
    "seq, event_id, timestamp, event_type, user_id, tenant_id, session_id, "
    "resource, action, success, ip_address, details, prev_hash, entry_hash"
)


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


class SQLiteAuditSink:
    """Append-only, hash-chained SQLite implementation of ``AuditSink``."""

    def __init__(
        self,
        path: str | Path,
        *,
        hash_chain: bool = True,
        max_detail_chars: int = 2000,
        hmac_key: SecretStr | str | bytes | None = None,
    ) -> None:
        """Open (or create) the audit database at ``path``.

        Args:
            path: SQLite file. Parent directories are created.
            hash_chain: Chain records together for tamper evidence.
            max_detail_chars: Truncation guard for caller-supplied details.
            hmac_key: Key for the HMAC-SHA256 chain. Omitted (the default)
                resolves ``AUDIT_CHAIN_HMAC_KEY`` from configuration, so the
                wiring in :mod:`core.observability.audit_setup` needs no
                changes; pass one explicitly in tests or embedding code.

        Raises:
            AuditChainKeyError: ``AUDIT_CHAIN_REQUIRE_KEY`` is set and no key
                could be resolved.
        """
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._hash_chain = hash_chain
        self._max_detail_chars = max_detail_chars
        self._hmac_key = self._resolve_key(hmac_key)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_SCHEMA)
        self._lock = RLock()

    def _resolve_key(self, hmac_key: SecretStr | str | bytes | None) -> bytes | None:
        """Resolve the chain key, enforcing ``AUDIT_CHAIN_REQUIRE_KEY``.

        The requirement must survive a *broken* configuration, not only a
        well-formed one. ``AuditConfig`` refuses to validate in exactly the
        state this guard exists for — required, no key — so a blanket
        ``except Exception`` around the config load would swallow the refusal
        and open the unkeyed chain the operator forbade. Hence: the validation
        error is re-raised as :class:`AuditChainKeyError`, and any *other*
        configuration failure still consults the environment directly before
        deciding it may run unkeyed.

        Raises:
            AuditChainKeyError: A keyed chain is required and none is available.
        """
        key = coerce_chain_key(hmac_key)
        require = False
        if key is None:
            try:
                config = get_audit_config()
                key = coerce_chain_key(config.chain_hmac_key)
                require = bool(config.chain_require_key)
            except ValidationError as exc:
                raise AuditChainKeyError(
                    "Audit configuration is invalid and AUDIT_CHAIN_HMAC_KEY "
                    f"could not be resolved; refusing to open the chain: {exc}"
                ) from exc
            except Exception as exc:  # configuration must not break the sink
                logger.debug("Audit chain key configuration unavailable: %s", exc)
                require = require_chain_key_from_env()
        if not self._hash_chain:
            return key
        if key is None:
            if require:
                raise AuditChainKeyError(
                    "AUDIT_CHAIN_REQUIRE_KEY is set but no AUDIT_CHAIN_HMAC_KEY "
                    "is configured; refusing to open an unkeyed audit chain."
                )
            logger.warning(
                "Audit hash chain running WITHOUT an HMAC key: set "
                "AUDIT_CHAIN_HMAC_KEY so the trail is tamper-evident against "
                "an attacker who can write to %s.",
                self._path,
            )
        return key

    # ------------------------------------------------------------------ write

    async def write(self, event: AuditEvent) -> None:
        """Append ``event`` to the trail without blocking the event loop."""
        payload = event.to_dict()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._append, payload)

    def _append(self, payload: dict[str, Any]) -> None:
        """Insert one record, linking it to the current chain head.

        Read-head and insert happen under the same lock so two concurrent
        writers can never derive the same ``prev_hash``.
        """
        payload["details"] = self._bounded_details(payload.get("details") or {})
        with self._lock:
            prev_hash = self._head_hash_locked()
            entry_hash = (
                compute_entry_hash(prev_hash, payload, self._hmac_key)
                if self._hash_chain
                else ""
            )
            self._conn.execute(
                "INSERT INTO audit_log (event_id, timestamp, event_type, user_id, "
                "tenant_id, session_id, resource, action, success, ip_address, "
                "details, prev_hash, entry_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["event_id"],
                    payload["timestamp"],
                    payload["event_type"],
                    payload.get("user_id"),
                    payload.get("tenant_id"),
                    payload.get("session_id"),
                    payload.get("resource"),
                    payload.get("action"),
                    1 if payload.get("success", True) else 0,
                    payload.get("ip_address"),
                    json.dumps(payload["details"], sort_keys=True, default=str),
                    prev_hash,
                    entry_hash,
                ),
            )

    def _bounded_details(self, details: dict[str, Any]) -> dict[str, Any]:
        """Cap caller-supplied details so one record cannot grow unbounded.

        Truncation happens *before* hashing, so the digest always covers
        exactly the bytes that were stored.
        """
        blob = json.dumps(details, sort_keys=True, default=str)
        if len(blob) <= self._max_detail_chars:
            return details
        return {"_truncated": True, "preview": blob[: self._max_detail_chars]}

    # ------------------------------------------------------------------- read

    def _head_hash_locked(self) -> str:
        cur = self._conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None or not row["entry_hash"]:
            return GENESIS_HASH
        return str(row["entry_hash"])

    def head_hash(self) -> str:
        """Current chain head — the digest to anchor in an external store."""
        with self._lock:
            return self._head_hash_locked()

    def count(self) -> int:
        """Number of records currently retained."""
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM audit_log")
            return int(cur.fetchone()["n"])

    def query(self, spec: AuditQuery | None = None) -> list[dict[str, Any]]:
        """Return matching records, newest first."""
        spec = spec or AuditQuery()
        where, params = spec.where()
        sql = (
            f"SELECT {_COLUMNS} FROM audit_log{where} "  # noqa: S608  # nosec B608 - fixed columns
            "ORDER BY seq DESC LIMIT ? OFFSET ?"
        )
        with self._lock:
            cur = self._conn.execute(sql, [*params, spec.limit, spec.offset])
            rows = cur.fetchall()
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["success"] = bool(record["success"])
        record["details"] = json.loads(record["details"])
        return record

    # ----------------------------------------------------------- verification

    def verify_chain(self) -> ChainVerification:
        """Recompute every link and report the first divergence, if any.

        The earliest surviving row's ``prev_hash`` is taken as the anchor: a
        retention purge legitimately removes older links and must not be
        reported as tampering.
        """
        if not self._hash_chain:
            return ChainVerification(
                ok=True,
                checked=0,
                reason="hash chain disabled",
                anchor_hash="",
                head_hash="",
            )
        with self._lock:
            cur = self._conn.execute(
                f"SELECT {_COLUMNS} FROM audit_log ORDER BY seq ASC"  # noqa: S608  # nosec B608 - fixed columns
            )
            rows = cur.fetchall()

        if not rows:
            return ChainVerification(ok=True, checked=0)

        anchor = str(rows[0]["prev_hash"])
        expected_prev = anchor
        checked = 0
        head = anchor
        for row in rows:
            chained = self._chained_row(row)
            if chained.prev_hash != expected_prev:
                return ChainVerification(
                    ok=False,
                    checked=checked,
                    broken_at=chained.seq,
                    reason="prev_hash does not match the preceding entry_hash",
                    anchor_hash=anchor,
                    head_hash=head,
                )
            recomputed = compute_entry_hash(
                chained.prev_hash, chained.payload, self._hmac_key
            )
            if not hmac.compare_digest(recomputed, chained.entry_hash):
                return ChainVerification(
                    ok=False,
                    checked=checked,
                    broken_at=chained.seq,
                    reason=(
                        "record content does not match its stored entry_hash "
                        "(tampering, or the chain was written under a "
                        "different AUDIT_CHAIN_HMAC_KEY)"
                    ),
                    anchor_hash=anchor,
                    head_hash=head,
                )
            expected_prev = chained.entry_hash
            head = chained.entry_hash
            checked += 1
        return ChainVerification(
            ok=True, checked=checked, anchor_hash=anchor, head_hash=head
        )

    @staticmethod
    def _chained_row(row: sqlite3.Row) -> _ChainedRow:
        """Rebuild the exact payload that was hashed at write time."""
        payload = {
            "event_id": row["event_id"],
            "timestamp": row["timestamp"],
            "event_type": row["event_type"],
            "user_id": row["user_id"],
            "tenant_id": row["tenant_id"],
            "session_id": row["session_id"],
            "resource": row["resource"],
            "action": row["action"],
            "details": json.loads(row["details"]),
            "success": bool(row["success"]),
            "ip_address": row["ip_address"],
        }
        return _ChainedRow(
            seq=int(row["seq"]),
            payload=payload,
            prev_hash=str(row["prev_hash"]),
            entry_hash=str(row["entry_hash"]),
        )

    # -------------------------------------------------------------- retention

    def purge_older_than(self, days: int) -> int:
        """Delete records older than ``days``; returns how many were removed.

        ``days <= 0`` is a no-op (retain forever). Never call this with a
        horizon below the statutory floor for a deployment in scope of the AI
        Act — see :data:`core.config.audit.MIN_RETENTION_DAYS`.
        """
        if days <= 0:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM audit_log WHERE timestamp < ?", (cutoff,)
            )
            return max(cur.rowcount, 0)

    def close(self) -> None:
        """Close the underlying connection. Never raises."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # close must never raise
                pass


__all__ = [
    "GENESIS_HASH",
    "AuditChainKeyError",
    "require_chain_key_from_env",
    "AuditQuery",
    "ChainVerification",
    "SQLiteAuditSink",
    "coerce_chain_key",
    "compute_entry_hash",
]
