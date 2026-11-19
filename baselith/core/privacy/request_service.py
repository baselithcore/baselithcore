"""The data-subject request service: receive, verify, extend, answer, refuse.

Every step is an audit record (``privacy.request``) naming the request id and
what happened, never the person: the register's own ``subject_ref`` stays in the
register. ``milestones`` and ``overdue_milestones`` have the same shape as the
incident services', so the compliance console's deadline timeline and alerts
treat a data-subject request like any other running clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from baselith.core.incidents.types import ReportingMilestone
from baselith.core.observability.audit import AuditEventType, get_audit_logger
from baselith.core.observability.logging import get_logger
from baselith.core.privacy.requests import (
    CLOSED_STATUSES,
    DsrRequest,
    DsrRequestError,
    DsrRequestNotFoundError,
    DsrRequestStatus,
    DsrRequestStore,
    DsrRequestType,
    InMemoryDsrRequestStore,
    SQLiteDsrRequestStore,
    _utcnow,
)

logger = get_logger(__name__)


class DsrRequestService:
    """The register of data-subject requests and their Art. 12(3) clocks."""

    def __init__(self, store: DsrRequestStore | None = None) -> None:
        self._store: DsrRequestStore = store or InMemoryDsrRequestStore()

    async def receive(
        self,
        request_type: DsrRequestType,
        subject_ref: str,
        *,
        received_at: datetime | None = None,
        channel: str = "",
    ) -> DsrRequest:
        """Register a request. ``received_at`` is when it reached the organisation.

        The month runs from there, not from the moment somebody types it in: a
        request that arrived on Friday and is registered on Monday has already
        spent three days.
        """
        request = DsrRequest(
            request_type=request_type,
            subject_ref=subject_ref,
            channel=channel,
        )
        if received_at is not None:
            request.received_at = received_at
        await self._store.save(request)
        await self._audit(request, "received")
        return request

    async def request_identity(
        self, request_id: str, *, at: datetime | None = None
    ) -> DsrRequest:
        """Record that proof of identity was asked for (Art. 12(6)). The clock keeps running."""
        request = await self._open(request_id)
        request.identity_requested_at = at or _utcnow()
        request.status = DsrRequestStatus.IDENTITY_PENDING
        return await self._touch(request, "identity_requested")

    async def confirm_identity(
        self, request_id: str, *, at: datetime | None = None
    ) -> DsrRequest:
        request = await self._open(request_id)
        request.identity_confirmed_at = at or _utcnow()
        request.status = DsrRequestStatus.IN_PROGRESS
        return await self._touch(request, "identity_confirmed")

    async def extend(
        self, request_id: str, reason: str, *, notified_at: datetime | None = None
    ) -> DsrRequest:
        """Extend by two months (Art. 12(3)) — only if the person was told in time.

        ``reason`` is required: the person is owed the reasons. The extension
        counts toward the deadline only when ``notified_at`` falls within the
        first month; otherwise it is recorded but the deadline does not move
        (see :attr:`DsrRequest.extension_valid`).
        """
        if not reason.strip():
            raise DsrRequestError(
                "an extension needs its reasons: the person is owed them"
            )
        request = await self._open(request_id)
        if request.extended:
            raise DsrRequestError(
                "the request was already extended; Art. 12(3) allows one extension"
            )
        request.extended = True
        request.extension_reason = reason.strip()
        request.extension_notified_at = notified_at or _utcnow()
        return await self._touch(request, "extended")

    async def fulfil(
        self, request_id: str, *, answered_at: datetime | None = None
    ) -> DsrRequest:
        request = await self._open(request_id)
        request.status = DsrRequestStatus.FULFILLED
        request.answered_at = answered_at or _utcnow()
        return await self._touch(request, "fulfilled")

    async def refuse(
        self, request_id: str, reason: str, *, answered_at: datetime | None = None
    ) -> DsrRequest:
        """Decline to act (Art. 12(4)): the reasons go to the person within the same month,
        with the right to complain to an authority and to seek a judicial remedy."""
        if not reason.strip():
            raise DsrRequestError(
                "a refusal needs its reasons: the person is owed them"
            )
        request = await self._open(request_id)
        request.status = DsrRequestStatus.REFUSED
        request.refusal_reason = reason.strip()
        request.answered_at = answered_at or _utcnow()
        return await self._touch(request, "refused")

    # -- reads -----------------------------------------------------------

    async def get(self, request_id: str) -> DsrRequest | None:
        return await self._store.get(request_id)

    async def list_requests(
        self, status: DsrRequestStatus | None = None
    ) -> list[DsrRequest]:
        rows = await self._store.list_all()
        return [r for r in rows if status is None or r.status is status]

    async def list_open(self) -> list[DsrRequest]:
        return [
            r for r in await self._store.list_all() if r.status not in CLOSED_STATUSES
        ]

    def milestones(self, request: DsrRequest) -> list[ReportingMilestone]:
        return request.milestones()

    async def overdue_milestones(
        self, now: datetime | None = None
    ) -> list[tuple[DsrRequest, ReportingMilestone]]:
        """Open requests whose deadline has passed unanswered."""
        moment = now or _utcnow()
        overdue: list[tuple[DsrRequest, ReportingMilestone]] = []
        for request in await self.list_open():
            for milestone in request.milestones():
                if milestone.is_overdue(moment):
                    overdue.append((request, milestone))
        return overdue

    # -- internals -------------------------------------------------------

    async def _open(self, request_id: str) -> DsrRequest:
        request = await self._store.get(request_id)
        if request is None:
            raise DsrRequestNotFoundError(request_id)
        if request.status in CLOSED_STATUSES:
            raise DsrRequestError("the request is already answered")
        return request

    async def _touch(self, request: DsrRequest, action: str) -> DsrRequest:
        request.updated_at = _utcnow()
        await self._store.save(request)
        await self._audit(request, action)
        return request

    @staticmethod
    async def _audit(request: DsrRequest, action: str) -> None:
        logger.info(
            "AUDIT | PRIVACY | request | id=%s type=%s action=%s",
            request.id,
            request.request_type.value,
            action,
        )
        details: dict[str, Any] = {
            "request_type": request.request_type.value,
            "status": request.status.value,
            "due_at": request.due_at.isoformat(),
        }
        if action in {"fulfilled", "refused"}:
            details["late"] = request.is_late
        if action == "extended":
            details["extension_valid"] = request.extension_valid
        await get_audit_logger().log(
            AuditEventType.PRIVACY_REQUEST,
            resource=request.id,
            action=action,
            details=details,
        )


_service: DsrRequestService | None = None


def get_dsr_request_service() -> DsrRequestService:
    """Get or create the global register (durable when ``PRIVACY_DSR_REQUESTS_DB_PATH`` is set)."""
    global _service
    if _service is None:
        from baselith.core.config.privacy import get_privacy_config

        path = get_privacy_config().dsr_requests_db_path
        _service = DsrRequestService(SQLiteDsrRequestStore(path) if path else None)
    return _service


def reset_dsr_request_service() -> None:
    """Drop the cached service (tests, reconfiguration)."""
    global _service
    _service = None


__all__ = [
    "DsrRequestService",
    "get_dsr_request_service",
    "reset_dsr_request_service",
]
