"""The audit sink under inputs and loads the happy path never shows it."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from baselith.core.context import (
    reset_tenant_context,
    reset_user_context,
    set_tenant_context,
    set_user_context,
)
from baselith.core.observability import audit as audit_module
from baselith.core.observability._audit_models import MAX_QUERY_LIMIT
from baselith.core.observability.audit import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    audit_emit,
)
from baselith.core.observability.audit_chain import AuditQuery, SQLiteAuditSink


@pytest.fixture
def sink(tmp_path):
    s = SQLiteAuditSink(tmp_path / "audit.db")
    yield s
    s.close()


class _Capture:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> None:
        self.events.append(event)


class TestWhatIsHashedIsWhatIsStored:
    async def test_values_the_round_trip_changes_still_verify(self, sink):
        # An integer key comes back a string, a truthy non-bool comes back
        # True, a non-string id comes back text: each used to read as tampering.
        event = AuditEvent(
            AuditEventType.CUSTOM,
            action="odd",
            user_id=42,  # type: ignore[arg-type]
            details={10: "ten", 2: "two", "when": datetime(2027, 1, 1, tzinfo=UTC)},
            success=1,  # type: ignore[arg-type]
        )
        await sink.write(event)
        await sink.write(AuditEvent(AuditEventType.CUSTOM, action="next"))

        report = sink.verify_chain()

        assert report.ok and report.checked == 2
        stored = sink.query(AuditQuery(ascending=True))[0]
        assert stored["user_id"] == "42" and stored["success"] is True
        assert stored["details"]["10"] == "ten"

    async def test_an_ordinary_record_hashes_as_it_always_did(self, sink, tmp_path):
        from baselith.core.observability.audit_digest import (
            compute_entry_hash,
            storable_payload,
        )

        payload = AuditEvent(
            AuditEventType.AUTH_LOGIN, user_id="u1", details={"a": [1, 2]}
        ).to_dict()

        assert storable_payload(payload) == payload
        assert compute_entry_hash("x", storable_payload(payload)) == (
            compute_entry_hash("x", payload)
        )


class TestQueryBounds:
    async def test_a_negative_limit_is_not_the_whole_trail(self, sink):
        for i in range(5):
            await sink.write(AuditEvent(AuditEventType.CUSTOM, action=str(i)))

        assert sink.query(AuditQuery(limit=-1)) == []
        assert len(sink.query(AuditQuery(limit=3, offset=-7))) == 3

    def test_the_limit_has_a_ceiling(self):
        assert AuditQuery(limit=10**9).page() == (MAX_QUERY_LIMIT, 0)


class TestVerification:
    async def test_it_does_not_wait_for_the_appenders_lock(self, sink):
        await sink.write(AuditEvent(AuditEventType.CUSTOM, action="a"))
        done: list[bool] = []

        with sink._lock:  # an append in flight on another thread holds this
            worker = threading.Thread(
                target=lambda: done.append(sink.verify_chain().ok)
            )
            worker.start()
            worker.join(timeout=5)

        assert done == [True]

    async def test_a_purged_prefix_and_an_edit_are_told_apart(self, sink):
        old = datetime.now(UTC) - timedelta(days=400)
        for i in range(3):
            event = AuditEvent(AuditEventType.CUSTOM, action=f"old{i}")
            event.timestamp = old
            await sink.write(event)
        for i in range(3):
            await sink.write(AuditEvent(AuditEventType.CUSTOM, action=f"new{i}"))

        assert sink.purge_older_than(180) == 3
        assert sink.verify_chain().ok

        sink._conn.execute("UPDATE audit_log SET action = 'edited' WHERE seq = 5")
        report = sink.verify_chain()

        assert not report.ok and report.broken_at == 5

    async def test_an_empty_trail_verifies(self, sink):
        report = sink.verify_chain()

        assert report.ok and report.checked == 0


class TestAttribution:
    async def test_an_unstated_actor_is_the_bound_principal(self):
        capture = _Capture()
        logger = AuditLogger([capture])
        tenant = set_tenant_context("acme")
        user = set_user_context("dpo-7")
        try:
            await logger.log(AuditEventType.PRIVACY_ERASE, resource="subject-1")
        finally:
            reset_user_context(user)
            reset_tenant_context(tenant)

        (event,) = capture.events
        assert (event.user_id, event.tenant_id) == ("dpo-7", "acme")

    async def test_a_stated_actor_is_kept(self):
        capture = _Capture()
        tenant = set_tenant_context("acme")
        user = set_user_context("dpo-7")
        try:
            await AuditLogger([capture]).log(
                AuditEventType.AUTH_LOGIN, user_id="someone-else", tenant_id="other"
            )
        finally:
            reset_user_context(user)
            reset_tenant_context(tenant)

        (event,) = capture.events
        assert (event.user_id, event.tenant_id) == ("someone-else", "other")

    async def test_outside_a_request_nothing_is_invented(self):
        from baselith.core import context

        capture = _Capture()
        # The suite binds "default" for every test; a background sweep has none.
        unbound = context._tenant_context.set(None)
        try:
            await AuditLogger([capture]).log(AuditEventType.CUSTOM, action="sweep")
        finally:
            context._tenant_context.reset(unbound)

        (event,) = capture.events
        assert event.user_id is None and event.tenant_id is None

    async def test_the_sync_helper_attributes_too(self, monkeypatch):
        import asyncio

        capture = _Capture()
        monkeypatch.setattr(
            audit_module, "get_audit_logger", lambda: AuditLogger([capture])
        )
        tenant = set_tenant_context("acme")
        user = set_user_context("dpo-7")
        try:
            audit_emit(AuditEventType.COMPLIANCE_ASSESSMENT, action="screen")
            await asyncio.sleep(0)
            await asyncio.gather(*audit_module._pending_tasks)
        finally:
            reset_user_context(user)
            reset_tenant_context(tenant)

        (event,) = capture.events
        assert (event.user_id, event.tenant_id) == ("dpo-7", "acme")
