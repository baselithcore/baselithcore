"""Unit tests for the human-in-the-loop ApprovalGate.

Covers the state machine: submit → approve, submit → deny, submit → timeout.
No mocks — the gate is pure asyncio state, so we drive it with real tasks.
"""

from __future__ import annotations

import asyncio

import pytest

from plugins.baselithbot.approvals import (
    ApprovalGate,
    ApprovalRequest,
    ApprovalStatus,
)


@pytest.fixture
def gate() -> ApprovalGate:
    return ApprovalGate(history_size=10)


class TestApprovalGate:
    async def test_submit_then_approve_releases_waiter(
        self, gate: ApprovalGate
    ) -> None:
        submit_task = asyncio.create_task(
            gate.submit(
                capability="desktop",
                action="click",
                params={"x": 10, "y": 20},
                timeout_seconds=5.0,
            )
        )
        # Let the waiter park.
        await asyncio.sleep(0)
        pending = await gate.pending()
        assert len(pending) == 1
        request_id = pending[0].id

        approved = await gate.approve(request_id, reason="looks safe")
        assert approved is True

        result = await submit_task
        assert isinstance(result, ApprovalRequest)
        assert result.status is ApprovalStatus.APPROVED
        assert result.reason == "looks safe"
        assert await gate.pending() == []

    async def test_submit_then_deny(self, gate: ApprovalGate) -> None:
        submit_task = asyncio.create_task(
            gate.submit(
                capability="shell",
                action="rm -rf /",
                params={},
                timeout_seconds=5.0,
            )
        )
        await asyncio.sleep(0)
        request_id = (await gate.pending())[0].id

        denied = await gate.deny(request_id, reason="destructive")
        assert denied is True

        result = await submit_task
        assert result.status is ApprovalStatus.DENIED
        assert result.reason == "destructive"

    async def test_timeout_resolves_as_denied_by_policy(
        self, gate: ApprovalGate
    ) -> None:
        result = await gate.submit(
            capability="browser",
            action="navigate",
            params={"url": "about:blank"},
            timeout_seconds=0.05,
        )
        assert result.status is ApprovalStatus.TIMED_OUT
        assert result.reason == "timeout"
        assert result.resolved_at is not None

    async def test_approve_unknown_request_is_noop(self, gate: ApprovalGate) -> None:
        assert await gate.approve("does-not-exist") is False
        assert await gate.deny("does-not-exist") is False

    async def test_snapshot_exposes_pending_and_history(
        self, gate: ApprovalGate
    ) -> None:
        task = asyncio.create_task(
            gate.submit(
                capability="desktop",
                action="type",
                params={"text": "hi"},
                timeout_seconds=5.0,
            )
        )
        await asyncio.sleep(0)
        pending_snapshot = await gate.snapshot()
        assert len(pending_snapshot["pending"]) == 1

        request_id = (await gate.pending())[0].id
        await gate.approve(request_id)
        await task

        resolved_snapshot = await gate.snapshot()
        assert resolved_snapshot["pending"] == []
        assert len(resolved_snapshot["history"]) == 1
        assert resolved_snapshot["history"][0]["status"] == "approved"

    async def test_subscribe_receives_lifecycle_events(
        self, gate: ApprovalGate
    ) -> None:
        queue = gate.subscribe()
        try:
            task = asyncio.create_task(
                gate.submit(
                    capability="desktop",
                    action="click",
                    params={},
                    timeout_seconds=5.0,
                )
            )
            pending_event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert pending_event["type"] == "approval.pending"

            request_id = (await gate.pending())[0].id
            await gate.approve(request_id)
            resolved_event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert resolved_event["type"] == "approval.resolved"
            assert resolved_event["payload"]["status"] == "approved"
            await task
        finally:
            gate.unsubscribe(queue)
