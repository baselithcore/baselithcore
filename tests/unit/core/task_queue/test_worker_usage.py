"""What a job bills reaches the process that runs usage sinks.

A queue worker hosts no plugins, so a plugin's sink never runs in it. The worker
collects the turns a job billed and hands them off (see
``baselith.core.services.llm.usage_outbox``), tagged with the identity the job
was enqueued under.
"""

from __future__ import annotations

import json
import types
from typing import Any
from unittest.mock import patch

import pytest
from rq import Worker

from baselith.core.services.llm import usage_outbox as outbox
from baselith.core.services.llm.usage import Usage
from baselith.core.services.llm.usage_sinks import UsageReport, emit_usage_report
from baselith.core.task_queue.worker import TenantAwareWorker

pytestmark = [pytest.mark.unit]


class Pipe:
    def __init__(self, store: dict[str, list[str]]) -> None:
        self.store = store

    def __enter__(self) -> Pipe:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def rpush(self, key: str, *values: str) -> None:
        self.store.setdefault(key, []).extend(values)

    def ltrim(self, *args: Any) -> None:
        return None

    def expire(self, *args: Any) -> None:
        return None

    def execute(self) -> list[Any]:
        return []


class Redis:
    def __init__(self, broken: bool = False) -> None:
        self.store: dict[str, list[str]] = {}
        self.broken = broken

    def pipeline(self, transaction: bool = True) -> Pipe:
        if self.broken:
            raise ConnectionError("down")
        return Pipe(self.store)

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(raw) for raw in self.store.get(outbox.OUTBOX_KEY, [])]


@pytest.fixture
def worker() -> TenantAwareWorker:
    built = object.__new__(TenantAwareWorker)
    built.connection = Redis()  # type: ignore[attr-defined]
    return built


def job(meta: dict[str, Any] | None = None) -> Any:
    return types.SimpleNamespace(
        id="job-1", func_name="pkg.fn", origin="analysis", meta=meta or {}
    )


def queue() -> Any:
    return types.SimpleNamespace(name="analysis")


def bill(*_: Any) -> str:
    emit_usage_report(
        UsageReport(
            model="ollama/qwen", usage=Usage(input_tokens=120, output_tokens=40)
        )
    )
    return "done"


def test_a_job_hands_off_what_it_billed_under_the_identity_it_was_enqueued_with(
    worker: TenantAwareWorker,
) -> None:
    meta = {"tenant_id": "acme", "user_id": "u1", "plugin": "agent_range"}
    with patch.object(Worker, "perform_job", side_effect=bill):
        assert worker.perform_job(job(meta), queue()) == "done"
    [record] = worker.connection.records()  # type: ignore[attr-defined]
    assert (record["tenant_id"], record["user_id"], record["plugin"]) == (
        "acme",
        "u1",
        "agent_range",
    )
    assert (record["input_tokens"], record["output_tokens"]) == (120, 40)


def test_a_job_with_no_plugin_is_handed_off_without_one(
    worker: TenantAwareWorker,
) -> None:
    with patch.object(Worker, "perform_job", side_effect=bill):
        worker.perform_job(job({"tenant_id": "acme"}), queue())
    [record] = worker.connection.records()  # type: ignore[attr-defined]
    assert record["plugin"] is None


def test_a_job_that_bills_nothing_leaves_the_outbox_alone(
    worker: TenantAwareWorker,
) -> None:
    with patch.object(Worker, "perform_job", return_value="done"):
        worker.perform_job(job({"plugin": "agent_range"}), queue())
    assert worker.connection.store == {}  # type: ignore[attr-defined]


def test_a_failing_job_still_hands_off_what_it_billed(
    worker: TenantAwareWorker,
) -> None:
    def fail(*_: Any) -> None:
        bill()
        raise RuntimeError("job failed")

    with patch.object(Worker, "perform_job", side_effect=fail):
        with pytest.raises(RuntimeError):
            worker.perform_job(job({"plugin": "agent_range"}), queue())
    assert len(worker.connection.records()) == 1  # type: ignore[attr-defined]


def test_a_redis_outage_leaves_the_jobs_result_untouched(
    worker: TenantAwareWorker,
) -> None:
    worker.connection = Redis(broken=True)  # type: ignore[attr-defined]
    with patch.object(Worker, "perform_job", side_effect=bill):
        assert worker.perform_job(job({"plugin": "agent_range"}), queue()) == "done"


def test_the_switch_keeps_a_worker_from_handing_off(
    worker: TenantAwareWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(outbox.ENV_SWITCH, "false")
    with patch.object(Worker, "perform_job", side_effect=bill):
        worker.perform_job(job({"plugin": "agent_range"}), queue())
    assert worker.connection.store == {}  # type: ignore[attr-defined]
