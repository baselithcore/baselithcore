"""Unit tests for the dead-letter queue.

Uses an in-memory fake implementing the Redis surface the DLQ relies on
(hash + sorted-set ops and pipelines).
"""

import base64
import types

import pytest

from core.task_queue.dead_letter import (
    DeadLetterError,
    DeadLetterQueue,
    DeadLetterRecord,
    dead_letter_handler,
)


class FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def hset(self, key, mapping=None):
        self._ops.append(("hset", key, mapping))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def delete(self, key):
        self._ops.append(("delete", key))
        return self

    def zrem(self, key, member):
        self._ops.append(("zrem", key, member))
        return self

    def execute(self):
        results = []
        for op in self._ops:
            name, key, *rest = op
            results.append(getattr(self._redis, name)(key, *rest))
        self._ops = []
        return results


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.zsets = {}

    def pipeline(self):
        return FakePipeline(self)

    def hset(self, key, mapping=None):
        self.hashes.setdefault(key, {}).update(mapping or {})
        return len(mapping or {})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def zrevrange(self, key, start, end):
        items = sorted(
            self.zsets.get(key, {}).items(), key=lambda kv: kv[1], reverse=True
        )
        ids = [k for k, _ in items]
        return ids[start : end + 1]

    def zrange(self, key, start, end):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        ids = [k for k, _ in items]
        return ids[start:] if end == -1 else ids[start : end + 1]

    def zrem(self, key, member):
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    def delete(self, key):
        existed = key in self.hashes or key in self.zsets
        self.hashes.pop(key, None)
        self.zsets.pop(key, None)
        return 1 if existed else 0


def _fake_job(job_id="job-1", func="mymod.myfunc", origin="documents", tenant="acme"):
    return types.SimpleNamespace(
        id=job_id,
        func_name=func,
        origin=origin,
        data=b"\x80\x04serialized",
        meta={"tenant_id": tenant},
        args=(1, "x"),
        kwargs={"k": "v"},
        retries_left=0,
    )


@pytest.fixture
def dlq():
    return DeadLetterQueue(connection=FakeRedis())


class TestRecordAndInspect:
    def test_record_and_get(self, dlq):
        rec = dlq.record(_fake_job(), "boom", "traceback-text")
        assert rec.job_id == "job-1"
        fetched = dlq.get("job-1")
        assert fetched is not None
        assert fetched.func_name == "mymod.myfunc"
        assert fetched.origin_queue == "documents"
        assert fetched.tenant_id == "acme"
        assert fetched.error == "boom"
        assert base64.b64decode(fetched.payload_b64) == b"\x80\x04serialized"

    def test_count(self, dlq):
        assert dlq.count() == 0
        dlq.record(_fake_job("a"), "e")
        dlq.record(_fake_job("b"), "e")
        assert dlq.count() == 2

    def test_list_most_recent_first(self, dlq):
        dlq.record(_fake_job("old"), "e")
        dlq.record(_fake_job("new"), "e")
        records = dlq.list()
        assert [r.job_id for r in records] == ["new", "old"]

    def test_list_pagination(self, dlq):
        for i in range(5):
            dlq.record(_fake_job(f"j{i}"), "e")
        page = dlq.list(limit=2, offset=0)
        assert len(page) == 2

    def test_get_missing_returns_none(self, dlq):
        assert dlq.get("ghost") is None


class TestPurge:
    def test_purge_single(self, dlq):
        dlq.record(_fake_job("a"), "e")
        assert dlq.purge("a") is True
        assert dlq.get("a") is None
        assert dlq.count() == 0

    def test_purge_all(self, dlq):
        for i in range(3):
            dlq.record(_fake_job(f"j{i}"), "e")
        removed = dlq.purge_all()
        assert removed == 3
        assert dlq.count() == 0


class TestReplay:
    def test_replay_missing_record_raises(self, dlq):
        with pytest.raises(DeadLetterError, match="No dead-letter record"):
            dlq.replay("ghost")

    def test_replay_from_payload_when_no_payload(self, dlq):
        rec = DeadLetterRecord(
            job_id="np",
            func_name="m.f",
            origin_queue="default",
            error="e",
            traceback="",
            failed_at=1.0,
            tenant_id="t",
            args_repr="()",
            kwargs_repr="{}",
            payload_b64="",
        )
        with pytest.raises(DeadLetterError, match="no stored payload"):
            dlq._replay_from_payload(rec)


class TestHandler:
    def test_handler_records_on_terminal_failure(self, monkeypatch):
        captured = {}

        class _DLQ:
            def record(self, job, error, tb):
                captured["job"] = job
                captured["error"] = error
                return None

        monkeypatch.setattr(
            "core.task_queue.dead_letter.get_dead_letter_queue", lambda: _DLQ()
        )
        job = _fake_job()
        job.retries_left = 0
        result = dead_letter_handler(job, ValueError, ValueError("nope"), None)
        assert result is True
        assert captured["error"] == "nope"

    def test_handler_skips_when_retries_remain(self, monkeypatch):
        called = {"n": 0}

        class _DLQ:
            def record(self, *a, **k):
                called["n"] += 1

        monkeypatch.setattr(
            "core.task_queue.dead_letter.get_dead_letter_queue", lambda: _DLQ()
        )
        job = _fake_job()
        job.retries_left = 2  # will be retried -> not dead-lettered
        assert dead_letter_handler(job, ValueError, ValueError("x"), None) is True
        assert called["n"] == 0

    def test_handler_never_raises(self, monkeypatch):
        def _boom():
            raise RuntimeError("dlq down")

        monkeypatch.setattr("core.task_queue.dead_letter.get_dead_letter_queue", _boom)
        job = _fake_job()
        # Must swallow internal errors and still return True.
        assert dead_letter_handler(job, ValueError, ValueError("x"), None) is True
