"""GDPR Art. 12(3): the register of data-subject requests and its clock."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from baselith.core.incidents.types import DsrMilestoneKind, add_calendar_months
from baselith.core.privacy import (
    DsrRequestError,
    DsrRequestNotFoundError,
    DsrRequestService,
    DsrRequestStatus,
    DsrRequestType,
    SQLiteDsrRequestStore,
)


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _audit(monkeypatch) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    class _Logger:
        async def log(self, event_type, **kwargs):
            records.append({"event_type": event_type.value, **kwargs})

    monkeypatch.setattr(
        "baselith.core.privacy.request_service.get_audit_logger", lambda: _Logger()
    )
    # The cases state fixed instants in 2027-2028; the service refuses one that
    # lies ahead of its clock, so the clock is pinned after all of them.
    monkeypatch.setattr(
        "baselith.core.privacy.request_service._utcnow", lambda: _utc(2030, 1, 1)
    )
    return records


def test_calendar_months_are_counted_in_one_step_not_by_repetition():
    start = _utc(2027, 1, 31, 9)

    assert add_calendar_months(start, 1) == _utc(2027, 2, 28, 9)
    assert add_calendar_months(start, 3) == _utc(2027, 4, 30, 9)  # not the 28th
    assert add_calendar_months(_utc(2027, 11, 15), 3) == _utc(2028, 2, 15)
    assert add_calendar_months(_utc(2028, 1, 31), 1) == _utc(2028, 2, 29)


async def test_the_month_runs_from_receipt_not_from_registration():
    service = DsrRequestService()

    request = await service.receive(
        DsrRequestType.ACCESS,
        "user-1",
        received_at=_utc(2027, 1, 31, 9),
        channel="email",
    )

    assert request.due_at == _utc(2027, 2, 28, 9)  # a month, not 30 days
    (milestone,) = service.milestones(request)
    assert milestone.kind is DsrMilestoneKind.RESPONSE
    assert milestone.is_overdue(_utc(2027, 3, 1)) is True


async def test_the_title_names_the_kind_of_request_never_the_person():
    request = await DsrRequestService().receive(
        DsrRequestType.ERASURE, "jane.doe@acme.test"
    )

    assert request.title.startswith("erasure request ")
    assert "jane" not in request.title and "acme" not in request.title


async def test_identity_verification_is_recorded_but_does_not_stop_the_clock():
    service = DsrRequestService()
    request = await service.receive(
        DsrRequestType.ACCESS, "u", received_at=_utc(2027, 3, 1, 9)
    )
    due = request.due_at

    asked = await service.request_identity(request.id, at=_utc(2027, 3, 2))
    confirmed = await service.confirm_identity(request.id, at=_utc(2027, 3, 20))

    assert asked.identity_requested_at == _utc(2027, 3, 2)
    assert confirmed.status is DsrRequestStatus.IN_PROGRESS
    assert confirmed.due_at == due  # the strictest reading: no suspension


async def test_an_extension_told_in_time_moves_the_deadline_by_two_months():
    service = DsrRequestService()
    request = await service.receive(
        DsrRequestType.ACCESS, "u", received_at=_utc(2027, 1, 31, 9)
    )

    extended = await service.extend(
        request.id, "a very large mailbox", notified_at=_utc(2027, 2, 20)
    )

    assert extended.extension_valid is True
    assert extended.base_due_at == _utc(2027, 2, 28, 9)
    assert extended.due_at == _utc(2027, 4, 30, 9)  # three calendar months from receipt


async def test_an_extension_told_late_is_recorded_but_does_not_count():
    service = DsrRequestService()
    request = await service.receive(
        DsrRequestType.ACCESS, "u", received_at=_utc(2027, 1, 31, 9)
    )

    late = await service.extend(request.id, "complex", notified_at=_utc(2027, 3, 5))

    assert late.extended is True and late.extension_valid is False
    assert late.due_at == late.base_due_at


async def test_an_extension_needs_reasons_and_is_allowed_once():
    service = DsrRequestService()
    request = await service.receive(DsrRequestType.ACCESS, "u")

    with pytest.raises(DsrRequestError, match="reasons"):
        await service.extend(request.id, "  ")
    await service.extend(request.id, "complex")
    with pytest.raises(DsrRequestError, match="already extended"):
        await service.extend(request.id, "again")


async def test_answering_on_time_and_late():
    service = DsrRequestService()
    ontime = await service.receive(
        DsrRequestType.ACCESS, "a", received_at=_utc(2027, 3, 1, 9)
    )
    late = await service.receive(
        DsrRequestType.ERASURE, "b", received_at=_utc(2027, 3, 1, 9)
    )

    done = await service.fulfil(ontime.id, answered_at=_utc(2027, 3, 20))
    behind = await service.fulfil(late.id, answered_at=_utc(2027, 4, 2))

    assert done.is_late is False and done.status is DsrRequestStatus.FULFILLED
    assert behind.is_late is True
    assert [m.is_submitted for m in service.milestones(done)] == [True]


async def test_a_refusal_needs_its_reasons_and_closes_the_clock():
    service = DsrRequestService()
    request = await service.receive(DsrRequestType.OBJECTION, "u")

    with pytest.raises(DsrRequestError, match="reasons"):
        await service.refuse(request.id, "")
    refused = await service.refuse(request.id, "manifestly unfounded")

    assert refused.status is DsrRequestStatus.REFUSED
    assert refused.refusal_reason == "manifestly unfounded"
    assert refused.answered_at is not None


async def test_an_answered_request_cannot_be_changed_and_unknown_ids_are_refused():
    service = DsrRequestService()
    request = await service.receive(DsrRequestType.ACCESS, "u")
    await service.fulfil(request.id)

    with pytest.raises(DsrRequestError, match="already answered"):
        await service.extend(request.id, "x")
    with pytest.raises(DsrRequestNotFoundError):
        await service.fulfil("nope")


async def test_only_open_requests_past_their_deadline_are_overdue():
    service = DsrRequestService()
    now = _utc(2027, 6, 1)
    old = await service.receive(
        DsrRequestType.ACCESS, "a", received_at=_utc(2027, 4, 1)
    )
    await service.receive(DsrRequestType.ACCESS, "b", received_at=_utc(2027, 5, 20))
    answered = await service.receive(
        DsrRequestType.ACCESS, "c", received_at=_utc(2027, 4, 1)
    )
    await service.fulfil(answered.id, answered_at=_utc(2027, 4, 15))

    overdue = await service.overdue_milestones(now)

    assert [r.id for r, _ in overdue] == [old.id]
    assert [r.id for r in await service.list_open()] != []


async def test_every_step_is_audited_without_the_person(_audit):
    service = DsrRequestService()
    request = await service.receive(DsrRequestType.ACCESS, "jane.doe@acme.test")
    await service.request_identity(request.id)
    await service.extend(request.id, "complex")
    await service.fulfil(request.id)

    assert [r["action"] for r in _audit] == [
        "received",
        "identity_requested",
        "extended",
        "fulfilled",
    ]
    assert all(r["event_type"] == "privacy.request" for r in _audit)
    assert all(r["resource"] == request.id for r in _audit)
    assert "jane" not in str(_audit) and "acme" not in str(_audit)
    assert _audit[2]["details"]["extension_valid"] is True


async def test_the_sqlite_store_keeps_the_clock_across_a_restart(tmp_path):
    path = str(tmp_path / "dsr.db")
    first = DsrRequestService(SQLiteDsrRequestStore(path))
    request = await first.receive(
        DsrRequestType.PORTABILITY,
        "u",
        received_at=_utc(2027, 1, 31, 9),
        channel="post",
    )
    await first.extend(request.id, "complex", notified_at=_utc(2027, 2, 10))

    again = DsrRequestService(SQLiteDsrRequestStore(path))
    restored = await again.get(request.id)

    assert restored is not None
    assert restored.to_dict() == (await first.get(request.id)).to_dict()
    assert restored.due_at == _utc(2027, 4, 30, 9)
    assert [r.id for r in await again.list_requests(DsrRequestStatus.RECEIVED)] == [
        request.id
    ]


def test_the_default_service_is_durable_only_when_a_path_is_configured(
    monkeypatch, tmp_path
):
    from baselith.core.config.privacy import PrivacyConfig
    from baselith.core.privacy import get_dsr_request_service, reset_dsr_request_service

    reset_dsr_request_service()
    monkeypatch.setattr(
        "baselith.core.config.privacy.get_privacy_config",
        lambda: PrivacyConfig(PRIVACY_DSR_REQUESTS_DB_PATH=str(tmp_path / "r.db")),
    )
    try:
        assert (
            type(get_dsr_request_service()._store).__name__ == "SQLiteDsrRequestStore"
        )
    finally:
        reset_dsr_request_service()


# -- stated instants -------------------------------------------------------


async def test_a_stated_instant_must_be_aware_past_and_after_receipt():
    service = DsrRequestService()
    with pytest.raises(DsrRequestError, match="timezone"):
        await service.receive(
            DsrRequestType.ACCESS,
            "ref",
            received_at=datetime(2027, 3, 1, 9),  # noqa: DTZ001 - the case
        )
    with pytest.raises(DsrRequestError, match="future"):
        await service.receive(
            DsrRequestType.ACCESS, "ref", received_at=_utc(2031, 1, 1)
        )
    assert await service.list_requests() == []

    request = await service.receive(
        DsrRequestType.ACCESS, "ref", received_at=_utc(2027, 3, 1, 9)
    )
    before = _utc(2027, 2, 1)
    for step in (
        service.request_identity(request.id, at=before),
        service.confirm_identity(request.id, at=before),
        service.extend(request.id, "complex", notified_at=before),
        service.fulfil(request.id, answered_at=before),
        service.refuse(request.id, "unfounded", answered_at=before),
    ):
        with pytest.raises(DsrRequestError, match="precedes the receipt"):
            await step

    stored = await service.get(request.id)
    assert stored is not None and stored.status is DsrRequestStatus.RECEIVED
    assert not stored.extended and stored.answered_at is None


async def test_the_audit_record_carries_the_instant_each_step_states(_audit):
    service = DsrRequestService()
    request = await service.receive(
        DsrRequestType.ERASURE, "ref", received_at=_utc(2027, 3, 1, 9)
    )
    await service.extend(request.id, "complex", notified_at=_utc(2027, 3, 20))
    await service.fulfil(request.id, answered_at=_utc(2027, 5, 2))

    stated = {r["action"]: r["details"]["at"] for r in _audit}
    assert stated == {
        "received": "2027-03-01T09:00:00+00:00",
        "extended": "2027-03-20T00:00:00+00:00",
        "fulfilled": "2027-05-02T00:00:00+00:00",
    }
    assert all("ref" not in str(r) for r in _audit)
