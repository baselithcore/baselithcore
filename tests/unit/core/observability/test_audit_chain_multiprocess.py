"""Two writers on one audit database must never fork the chain.

Every uvicorn worker (and the task-queue worker) opens its own
``SQLiteAuditSink`` on the same file. The sink serialised read-head and insert
with an in-process ``RLock`` over an autocommit connection, so two *processes*
could read the same head and both insert a row naming it as ``prev_hash``: the
chain forked and ``verify_chain`` reported tampering that never happened. A
deployment with four workers showed five such forks in 1388 rows, all from
the retention sweep every worker runs at boot.

Two sinks on separate connections stand in for two processes: their locks are
independent, exactly as in production.
"""

from __future__ import annotations

import threading
import time

import pytest

from core.observability.audit import AuditEvent, AuditEventType
from core.observability.audit_chain import SQLiteAuditSink


def _payload(action: str) -> dict:
    return AuditEvent(AuditEventType.CUSTOM, action=action).to_dict()


@pytest.fixture
def pair(tmp_path):
    a = SQLiteAuditSink(tmp_path / "audit.db")
    b = SQLiteAuditSink(tmp_path / "audit.db")
    yield a, b
    a.close()
    b.close()


def test_writer_between_head_read_and_insert_cannot_fork(pair) -> None:
    a, b = pair
    a._append(_payload("seed"))

    read_head = a._head_hash_locked
    other: list[threading.Thread] = []

    def racing_head() -> str:
        head = read_head()
        # Another process appends right after A has read the head, before A
        # inserts: the exact interleaving that forked the production chain.
        t = threading.Thread(target=b._append, args=(_payload("from-b"),))
        t.start()
        other.append(t)
        time.sleep(0.3)
        return head

    a._head_hash_locked = racing_head  # type: ignore[method-assign]
    a._append(_payload("from-a"))
    a._head_hash_locked = read_head  # type: ignore[method-assign]
    for t in other:
        t.join(timeout=10)

    rows = a._conn.execute("SELECT prev_hash FROM audit_log ORDER BY seq").fetchall()
    prevs = [r["prev_hash"] for r in rows]
    assert len(prevs) == 3
    assert len(set(prevs)) == len(prevs), "two rows name the same predecessor"
    assert a.verify_chain().ok


def test_concurrent_writers_keep_one_chain(pair) -> None:
    a, b = pair

    def burst(sink: SQLiteAuditSink, tag: str) -> None:
        for i in range(40):
            sink._append(_payload(f"{tag}-{i}"))

    threads = [
        threading.Thread(target=burst, args=(s, t)) for s, t in ((a, "a"), (b, "b"))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    result = a.verify_chain()
    assert result.ok, result
    assert result.checked == 80
