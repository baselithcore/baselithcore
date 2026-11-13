"""Tests for the async agent-run job."""

from __future__ import annotations

import re

import pytest

from baselith.core.task_queue.jobs import agent_run as job_module
from baselith.core.task_queue.status import TaskStatus, TaskTracker

pytestmark = [pytest.mark.unit]


class _Tracker(TaskTracker):
    """The real ``TaskTracker`` with only ``set_status`` swapped for a recorder.

    Every ``mark_*`` keeps its true signature, so a call in the wrong shape
    raises ``TypeError`` here exactly as it does on a worker. A hand-written
    fake used to take ``message`` before ``result``, the reverse of the real
    ``mark_completed``, which let the production call pass while it crashed
    every successful run.
    """

    def __init__(self) -> None:
        super().__init__(conn=None)  # type: ignore[arg-type]
        self.calls: list[tuple[str, TaskStatus, dict]] = []

    def set_status(self, task_id, status, **kwargs):  # type: ignore[override]
        self.calls.append((task_id, status, kwargs))

    @property
    def started(self) -> list[str]:
        return [t for t, s, _ in self.calls if s is TaskStatus.RUNNING]

    @property
    def completed(self) -> list[tuple[str, dict]]:
        return [
            (t, kw.get("result") or {})
            for t, s, kw in self.calls
            if s is TaskStatus.COMPLETED
        ]

    @property
    def failed(self) -> list[tuple[str, str]]:
        return [(t, kw["error"]) for t, s, kw in self.calls if s is TaskStatus.FAILED]

    @property
    def retrying(self) -> list[tuple[str, int]]:
        return [
            (t, int(re.search(r"\((\d+) left\)", kw["message"]).group(1)))  # type: ignore[union-attr]
            for t, s, kw in self.calls
            if s is TaskStatus.RETRYING
        ]

    def messages(self, status: TaskStatus) -> list[str]:
        return [kw.get("message", "") for _, s, kw in self.calls if s is status]


class _Webhooks:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []
        self.tenants: list[str] = []

    async def publish(self, event_type, data, *, tenant_id="default"):
        self.emitted.append((event_type, data))
        self.tenants.append(tenant_id)
        return []


class _Response:
    answer = "the answer"
    metadata = {"intent": "chat"}


@pytest.fixture
def harness(monkeypatch):
    tracker, webhooks = _Tracker(), _Webhooks()
    monkeypatch.setattr(job_module, "_get_tracker", lambda: tracker)
    monkeypatch.setattr(job_module, "_get_webhooks", lambda: webhooks)
    return tracker, webhooks


class TestRunAgentTask:
    def test_success_tracks_and_notifies(self, harness, monkeypatch):
        tracker, webhooks = harness

        async def fake_chat(req, **_kwargs):
            assert req.query == "hello"
            return _Response()

        monkeypatch.setattr(job_module, "_handle_chat", fake_chat)

        result = job_module.run_agent_task("hello")
        assert result["answer"] == "the answer"
        assert len(tracker.completed) == 1
        assert tracker.completed[0][1] == result
        assert tracker.messages(TaskStatus.COMPLETED) == ["Agent run completed"]
        assert webhooks.emitted[0][0] == "agent.completed"
        assert webhooks.emitted[0][1]["answer"] == "the answer"

    def test_failure_marks_failed_and_notifies(self, harness, monkeypatch):
        tracker, webhooks = harness

        async def fake_chat(req, **_kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(job_module, "_handle_chat", fake_chat)

        with pytest.raises(RuntimeError):
            job_module.run_agent_task("hello")
        assert len(tracker.failed) == 1
        assert webhooks.emitted[0][0] == "agent.failed"

    def test_webhook_failure_does_not_fail_job(self, harness, monkeypatch):
        tracker, webhooks = harness

        async def fake_chat(req, **_kwargs):
            return _Response()

        async def broken_emit(event_type, data, *, tenant_id="default"):
            raise ConnectionError("webhook store down")

        monkeypatch.setattr(job_module, "_handle_chat", fake_chat)
        monkeypatch.setattr(webhooks, "publish", broken_emit)

        result = job_module.run_agent_task("hello")
        assert result["answer"] == "the answer"
        assert len(tracker.completed) == 1


class _Job:
    def __init__(self, retries_left):
        self.id = "job-1"
        self.retries_left = retries_left


class TestRetryAwareFailure:
    """A failed attempt is terminal only when RQ will not run the job again."""

    @pytest.fixture
    def failing(self, harness, monkeypatch):
        async def fake_chat(req, **_kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(job_module, "_handle_chat", fake_chat)
        return harness

    def _run_with(self, monkeypatch, job):
        monkeypatch.setattr(job_module, "get_current_job", lambda: job)
        with pytest.raises(RuntimeError):
            job_module.run_agent_task("hello")

    def test_attempt_with_retries_left_is_not_terminal(self, failing, monkeypatch):
        tracker, webhooks = failing
        self._run_with(monkeypatch, _Job(retries_left=2))
        assert tracker.failed == []
        assert tracker.retrying == [("job-1", 2)]
        assert webhooks.emitted == []

    def test_last_attempt_marks_failed_and_notifies(self, failing, monkeypatch):
        tracker, webhooks = failing
        self._run_with(monkeypatch, _Job(retries_left=0))
        assert tracker.failed == [("job-1", "provider down")]
        assert tracker.retrying == []
        assert [e for e, _ in webhooks.emitted] == ["agent.failed"]

    def test_job_without_retry_policy_fails_terminally(self, failing, monkeypatch):
        tracker, webhooks = failing
        self._run_with(monkeypatch, _Job(retries_left=None))
        assert len(tracker.failed) == 1
        assert [e for e, _ in webhooks.emitted] == ["agent.failed"]

    def test_retry_then_success_never_reports_failed(self, harness, monkeypatch):
        tracker, webhooks = harness
        calls = {"n": 0}

        async def flaky_chat(req, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return _Response()

        monkeypatch.setattr(job_module, "_handle_chat", flaky_chat)
        job = _Job(retries_left=3)
        monkeypatch.setattr(job_module, "get_current_job", lambda: job)
        with pytest.raises(RuntimeError):
            job_module.run_agent_task("hello")
        job.retries_left = 2  # RQ decrements after the failed attempt
        job_module.run_agent_task("hello")
        assert tracker.failed == []
        assert len(tracker.completed) == 1
        assert [e for e, _ in webhooks.emitted] == ["agent.completed"]


class TestNotifyResumedRun:
    """The recovery sweep finishes a run for a worker that died."""

    async def test_a_recovered_answer_is_recorded_completed(self, harness):
        tracker, webhooks = harness
        await job_module.notify_resumed_run(
            {"task_id": "job-9"}, {"response": "recovered", "metadata": {"k": 1}}
        )
        assert tracker.completed == [
            ("job-9", {"answer": "recovered", "metadata": {"k": 1}})
        ]
        assert tracker.messages(TaskStatus.COMPLETED) == [
            "Agent run completed (resumed by recovery)"
        ]
        assert [e for e, _ in webhooks.emitted] == ["agent.completed"]

    async def test_a_recovered_failure_is_recorded_failed(self, harness):
        tracker, webhooks = harness
        await job_module.notify_resumed_run(
            {"task_id": "job-9"}, {"error": True, "response": "tool crashed"}
        )
        assert tracker.failed == [("job-9", "tool crashed")]
        assert [e for e, _ in webhooks.emitted] == ["agent.failed"]

    async def test_a_run_awaiting_approval_is_not_terminal(self, harness):
        tracker, webhooks = harness
        await job_module.notify_resumed_run(
            {"task_id": "job-9"}, {"awaiting_approval": True}
        )
        assert tracker.calls == [] and webhooks.emitted == []

    async def test_a_spec_without_a_task_id_is_ignored(self, harness):
        tracker, webhooks = harness
        await job_module.notify_resumed_run({}, {"response": "x"})
        assert tracker.calls == [] and webhooks.emitted == []


def test_tracker_mark_retrying_is_non_terminal_status():
    from baselith.core.task_queue.status import TaskStatus, TaskTracker

    recorded = {}

    class _Tracker(TaskTracker):
        def set_status(self, task_id, status, **kwargs):
            recorded.update(task_id=task_id, status=status, **kwargs)

    _Tracker(conn=None).mark_retrying("t1", "boom", 2)  # type: ignore[arg-type]
    assert recorded["status"] is TaskStatus.RETRYING
    assert recorded["status"].value == "retrying"
    assert recorded["error"] == "boom"


class TestWebhookTenant:
    """The terminal webhook belongs to the tenant that ran the job."""

    def test_worker_emit_carries_the_bound_tenant(self, harness, monkeypatch):
        from baselith.core.context import reset_tenant_context, set_tenant_context

        _, webhooks = harness

        async def fake_chat(req, **_kwargs):
            return _Response()

        monkeypatch.setattr(job_module, "_handle_chat", fake_chat)
        token = set_tenant_context("acme")
        try:
            job_module.run_agent_task("hello")
        finally:
            reset_tenant_context(token)
        assert webhooks.tenants == ["acme"]

    async def test_sweep_notifier_is_rebound_to_the_checkpoint_tenant(self, harness):
        from types import SimpleNamespace

        from baselith.core.context import get_current_tenant_id
        from baselith.core.orchestration.run_completion import (
            COMPLETION_NOTIFY_KEY,
            notify_resumed_completion,
        )

        _, webhooks = harness
        checkpoint = SimpleNamespace(
            tenant_id="acme",
            inputs={
                COMPLETION_NOTIFY_KEY: {"kind": "task_queue.agent_run", "task_id": "t1"}
            },
        )
        ran = await notify_resumed_completion(checkpoint, {"response": "done"})
        assert ran is True
        assert webhooks.tenants == ["acme"]
        assert get_current_tenant_id() != "acme"  # the binding does not leak
