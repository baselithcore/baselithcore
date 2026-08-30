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

    def mark_started(self, job_id, message=""):
        self.started.append(job_id)

    def mark_completed(self, job_id, message="", result=None):
        self.completed.append((job_id, result or {}))

    def mark_failed(self, job_id, error):
        self.failed.append((job_id, error))


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
