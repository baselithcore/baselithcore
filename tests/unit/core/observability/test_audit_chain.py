"""Tests for the durable, tamper-evident audit sink and its wiring."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from core.config.audit import MIN_RETENTION_DAYS, AuditConfig, reset_audit_config
from core.observability.audit import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    audit_emit,
    get_audit_logger,
    reset_audit_logger,
)
from core.observability.audit_chain import (
    GENESIS_HASH,
    AuditQuery,
    SQLiteAuditSink,
    compute_entry_hash,
)


@pytest.fixture
def sink(tmp_path):
    s = SQLiteAuditSink(tmp_path / "audit.db")
    yield s
    s.close()


def _event(action: str, **kwargs) -> AuditEvent:
    return AuditEvent(AuditEventType.CUSTOM, action=action, **kwargs)


class TestChainIntegrity:
    async def test_first_record_links_to_genesis(self, sink):
        await sink.write(_event("first"))
        rows = sink.query()
        assert rows[0]["prev_hash"] == GENESIS_HASH
        assert rows[0]["entry_hash"] != ""

    async def test_records_chain_to_predecessor(self, sink):
        for i in range(3):
            await sink.write(_event(f"a{i}"))
        rows = sink.query()  # newest first
        rows.reverse()
        for prev, current in pairwise(rows):
            assert current["prev_hash"] == prev["entry_hash"]

    async def test_verify_chain_passes_on_untouched_log(self, sink):
        for i in range(5):
            await sink.write(_event(f"b{i}"))
        result = sink.verify_chain()
        assert result.ok is True
        assert result.checked == 5
        assert result.broken_at is None
        assert result.head_hash == sink.head_hash()

    async def test_verify_chain_detects_edited_record(self, sink, tmp_path):
        for i in range(4):
            await sink.write(_event(f"c{i}"))
        sink.close()

        # Tamper directly with the file — the attack the chain exists to catch.
        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        conn.execute("UPDATE audit_log SET action = 'rewritten' WHERE seq = 2")
        conn.commit()
        conn.close()

        reopened = SQLiteAuditSink(tmp_path / "audit.db")
        try:
            result = reopened.verify_chain()
            assert result.ok is False
            assert result.broken_at == 2
            assert result.reason is not None
        finally:
            reopened.close()

    async def test_verify_chain_detects_deleted_record(self, sink, tmp_path):
        for i in range(4):
            await sink.write(_event(f"d{i}"))
        sink.close()

        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        conn.execute("DELETE FROM audit_log WHERE seq = 2")
        conn.commit()
        conn.close()

        reopened = SQLiteAuditSink(tmp_path / "audit.db")
        try:
            result = reopened.verify_chain()
            assert result.ok is False
            # seq 3 no longer links to its (removed) predecessor.
            assert result.broken_at == 3
        finally:
            reopened.close()

    async def test_hash_chain_can_be_disabled(self, tmp_path):
        s = SQLiteAuditSink(tmp_path / "plain.db", hash_chain=False)
        try:
            await s.write(_event("plain"))
            result = s.verify_chain()
            assert result.ok is True
            assert result.reason == "hash chain disabled"
        finally:
            s.close()

    def test_compute_entry_hash_is_key_order_independent(self):
        a = compute_entry_hash(GENESIS_HASH, {"x": 1, "y": 2})
        b = compute_entry_hash(GENESIS_HASH, {"y": 2, "x": 1})
        assert a == b


class TestRetention:
    async def test_purge_removes_only_expired_records(self, sink):
        await sink.write(_event("old"))
        await sink.write(_event("fresh"))
        # Age the first record past the horizon.
        stale = (datetime.now(UTC) - timedelta(days=400)).isoformat()
        sink._conn.execute("UPDATE audit_log SET timestamp = ? WHERE seq = 1", (stale,))
        purged = sink.purge_older_than(MIN_RETENTION_DAYS)
        assert purged == 1
        assert sink.count() == 1

    async def test_purge_is_noop_when_retention_disabled(self, sink):
        await sink.write(_event("keep"))
        assert sink.purge_older_than(0) == 0
        assert sink.count() == 1

    async def test_verification_survives_a_retention_purge(self, sink):
        for i in range(4):
            await sink.write(_event(f"e{i}"))
        stale = (datetime.now(UTC) - timedelta(days=400)).isoformat()
        sink._conn.execute(
            "UPDATE audit_log SET timestamp = ? WHERE seq <= 2", (stale,)
        )
        assert sink.purge_older_than(MIN_RETENTION_DAYS) == 2
        # Truncating the tail of the chain is legitimate, not tampering.
        result = sink.verify_chain()
        assert result.ok is True
        assert result.checked == 2


class TestQuerying:
    async def test_filters_by_type_user_and_tenant(self, sink):
        await sink.write(
            AuditEvent(AuditEventType.AUTH_LOGIN, user_id="u1", tenant_id="t1")
        )
        await sink.write(
            AuditEvent(AuditEventType.PRIVACY_ERASE, user_id="u2", tenant_id="t2")
        )
        assert len(sink.query(AuditQuery(user_id="u1"))) == 1
        assert len(sink.query(AuditQuery(tenant_id="t2"))) == 1
        assert len(sink.query(AuditQuery(event_type="privacy.erase"))) == 1
        assert len(sink.query(AuditQuery())) == 2

    async def test_filters_by_time_window(self, sink):
        await sink.write(_event("now"))
        past = datetime.now(UTC) - timedelta(days=1)
        future = datetime.now(UTC) + timedelta(days=1)
        assert len(sink.query(AuditQuery(since=past))) == 1
        assert len(sink.query(AuditQuery(since=future))) == 0
        assert len(sink.query(AuditQuery(until=past))) == 0

    async def test_details_round_trip_as_json(self, sink):
        await sink.write(_event("detailed", details={"a": [1, 2], "b": "x"}))
        assert sink.query()[0]["details"] == {"a": [1, 2], "b": "x"}

    async def test_oversized_details_are_truncated_before_hashing(self, tmp_path):
        s = SQLiteAuditSink(tmp_path / "cap.db", max_detail_chars=64)
        try:
            await s.write(_event("big", details={"blob": "x" * 5000}))
            stored = s.query()[0]["details"]
            assert stored["_truncated"] is True
            assert len(json.dumps(stored)) < 500
            # The digest must cover exactly what was stored.
            assert s.verify_chain().ok is True
        finally:
            s.close()


class TestAuditLoggerIntegration:
    async def test_logger_fans_out_to_the_durable_sink(self, sink):
        audit_logger = AuditLogger(sinks=[sink])
        await audit_logger.log(
            AuditEventType.PRIVACY_EXPORT, resource="subject-1", action="export"
        )
        rows = sink.query()
        assert len(rows) == 1
        assert rows[0]["event_type"] == "privacy.export"
        assert rows[0]["resource"] == "subject-1"

    async def test_a_broken_sink_never_breaks_the_caller(self, sink):
        class Broken:
            async def write(self, event):
                raise RuntimeError("disk on fire")

        audit_logger = AuditLogger(sinks=[Broken(), sink])
        await audit_logger.log(AuditEventType.CUSTOM, action="resilient")
        # The healthy sink still recorded the event.
        assert sink.count() == 1

    async def test_disabled_logger_writes_nothing(self, sink):
        audit_logger = AuditLogger(sinks=[sink])
        audit_logger.enabled = False
        await audit_logger.log(AuditEventType.CUSTOM, action="ignored")
        assert sink.count() == 0

    async def test_audit_emit_schedules_on_the_running_loop(self, sink):
        import asyncio

        from core.observability.audit import _pending_tasks

        reset_audit_logger()
        try:
            from core.observability.audit import set_audit_logger

            set_audit_logger(AuditLogger(sinks=[sink]))
            audit_emit(AuditEventType.TRANSPARENCY_MARK, action="mark")
            # Wait for the scheduled task itself, not for a fixed number of
            # event-loop turns. The write ends in `run_in_executor`, so it
            # completes on a worker thread: no amount of `sleep(0)` can
            # guarantee that thread has been scheduled, and the previous
            # two-yield version failed as soon as the thread took ~5ms — a
            # loaded CI runner, which is why it surfaced under xdist.
            pending = list(_pending_tasks)
            assert pending, "audit_emit should have scheduled a task"
            await asyncio.gather(*pending)
            assert sink.count() == 1
        finally:
            reset_audit_logger()

    def test_audit_emit_without_a_loop_does_not_raise(self):
        reset_audit_logger()
        try:
            audit_emit(AuditEventType.CUSTOM, action="no-loop")
        finally:
            reset_audit_logger()

    def test_event_carries_a_stable_identity(self):
        event = AuditEvent(AuditEventType.CUSTOM)
        assert event.event_id
        assert event.to_dict()["event_id"] == event.event_id

    def test_default_global_logger_is_still_logger_only(self):
        reset_audit_logger()
        try:
            assert len(get_audit_logger().sinks) == 1
        finally:
            reset_audit_logger()


class TestAuditConfig:
    def test_retention_defaults_to_the_six_month_floor(self, monkeypatch):
        monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)
        reset_audit_config()
        assert AuditConfig().retention_days == MIN_RETENTION_DAYS

    def test_subsystem_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("AUDIT_ENABLED", raising=False)
        reset_audit_config()
        assert AuditConfig().enabled is False

    def test_short_retention_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("AUDIT_ENABLED", "true")
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "30")
        reset_audit_config()
        with caplog.at_level("WARNING"):
            AuditConfig()
        assert "six-month floor" in caplog.text
        reset_audit_config()


class TestAuditSetup:
    def test_disabled_config_leaves_the_global_logger_untouched(self, monkeypatch):
        from core.observability.audit_setup import configure_audit_logging

        monkeypatch.setenv("AUDIT_ENABLED", "false")
        reset_audit_config()
        assert configure_audit_logging() is None
        reset_audit_config()

    def test_enabled_config_installs_the_durable_sink(self, monkeypatch, tmp_path):
        from core.observability.audit_setup import (
            configure_audit_logging,
            get_durable_audit_sink,
        )

        monkeypatch.setenv("AUDIT_ENABLED", "true")
        monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "wired.db"))
        reset_audit_config()
        reset_audit_logger()
        try:
            configured = configure_audit_logging()
            assert configured is not None
            durable = get_durable_audit_sink()
            assert isinstance(durable, SQLiteAuditSink)
            durable.close()
        finally:
            reset_audit_logger()
            reset_audit_config()
