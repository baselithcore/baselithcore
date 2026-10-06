"""Tests for the async agent-run job."""

from __future__ import annotations

import pytest

from core.task_queue.jobs import agent_run as job_module

pytestmark = [pytest.mark.unit]


class _Tracker:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.completed: list[tuple[str, dict]] = []
        self.failed: list[tuple[str, str]] = []
        self.retrying: list[tuple[str, int]] = []

    def mark_started(self, job_id, message=""):
        self.started.append(job_id)

    def mark_completed(self, job_id, message="", result=None):
        self.completed.append((job_id, result or {}))

    def mark_failed(self, job_id, error):
        self.failed.append((job_id, error))

    def mark_retrying(self, job_id, error, retries_left):
        self.retrying.append((job_id, retries_left))


class _Webhooks:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, event_type, data, *, tenant_id="default"):
        self.emitted.append((event_type, data))
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

        async def fake_chat(req):
            assert req.query == "hello"
            return _Response()

        monkeypatch.setattr(job_module, "_handle_chat", fake_chat)

        result = job_module.run_agent_task("hello")
        assert result["answer"] == "the answer"
        assert len(tracker.completed) == 1
        assert webhooks.emitted[0][0] == "agent.completed"
        assert webhooks.emitted[0][1]["answer"] == "the answer"

    def test_failure_marks_failed_and_notifies(self, harness, monkeypatch):
        tracker, webhooks = harness

        async def fake_chat(req):
            raise RuntimeError("provider down")

        monkeypatch.setattr(job_module, "_handle_chat", fake_chat)

        with pytest.raises(RuntimeError):
            job_module.run_agent_task("hello")
        assert len(tracker.failed) == 1
        assert webhooks.emitted[0][0] == "agent.failed"

    def test_webhook_failure_does_not_fail_job(self, harness, monkeypatch):
        tracker, webhooks = harness

        async def fake_chat(req):
            return _Response()

        async def broken_emit(event_type, data, *, tenant_id="default"):
            raise ConnectionError("webhook store down")

        monkeypatch.setattr(job_module, "_handle_chat", fake_chat)
        monkeypatch.setattr(webhooks, "emit", broken_emit)

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
        async def fake_chat(req):
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

        async def flaky_chat(req):
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


def test_tracker_mark_retrying_is_non_terminal_status():
    from core.task_queue.status import TaskStatus, TaskTracker

    recorded = {}

    class _Tracker(TaskTracker):
        def set_status(self, task_id, status, **kwargs):
            recorded.update(task_id=task_id, status=status, **kwargs)

    _Tracker(conn=None).mark_retrying("t1", "boom", 2)  # type: ignore[arg-type]
    assert recorded["status"] is TaskStatus.RETRYING
    assert recorded["status"].value == "retrying"
    assert recorded["error"] == "boom"
