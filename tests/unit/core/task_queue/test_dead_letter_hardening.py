"""DLQ retention, redaction, and unpickle-free replay.

The DLQ used to keep every failed job forever, verbatim: the pickled RQ payload
plus ``repr()`` of the arguments. That is an unbounded Redis keyspace holding
whatever secrets the arguments carried, and a replay path that unpickled a blob
an attacker with Redis write access could choose.
"""

from __future__ import annotations

import json
import time
import types
from unittest.mock import patch

import pytest

from core.config.task_queue import TaskQueueConfig
from core.task_queue.dead_letter import (
    UNAVAILABLE_ARGS,
    DeadLetterError,
    DeadLetterQueue,
    DeadLetterRecord,
)

pytestmark = [pytest.mark.unit]


class FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops: list = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self

        return _record

    def execute(self):
        results = []
        for name, args, kwargs in self._ops:
            results.append(getattr(self._redis, name)(*args, **kwargs))
        self._ops = []
        return results


class FakeRedis:
    """In-memory stand-in for the Redis surface the DLQ uses."""

    def __init__(self):
        self.hashes: dict = {}
        self.zsets: dict = {}
        self.expiries: dict = {}

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
        return [k for k, _ in items][start : end + 1]

    def zrange(self, key, start, end):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        ids = [k for k, _ in items]
        return ids[start:] if end == -1 else ids[start : end + 1]

    def zrem(self, key, member):
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    def zremrangebyscore(self, key, low, high):
        bucket = self.zsets.get(key, {})
        stale = [k for k, score in bucket.items() if low <= score <= high]
        for member in stale:
            bucket.pop(member)
        return len(stale)

    def expire(self, key, seconds):
        self.expiries[key] = seconds
        return 1

    def delete(self, key):
        existed = key in self.hashes or key in self.zsets
        self.hashes.pop(key, None)
        self.zsets.pop(key, None)
        return 1 if existed else 0


def _job(job_id="job-1", args=(1, "x"), kwargs=None, func="pkg.mod.fn"):
    return types.SimpleNamespace(
        id=job_id,
        func_name=func,
        origin="documents",
        data=b"\x80\x04pickled",
        meta={"tenant_id": "acme"},
        args=args,
        kwargs={"k": "v"} if kwargs is None else kwargs,
        retries_left=0,
    )


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def dlq(redis):
    return DeadLetterQueue(connection=redis)


def _config(**kwargs):
    return patch(
        "core.task_queue.dead_letter.get_task_queue_config",
        return_value=TaskQueueConfig(**kwargs),
    )


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


class TestRetention:
    def test_record_sets_a_ttl_on_the_hash(self, dlq, redis):
        with _config(dlq_retention_seconds=1234):
            dlq.record(_job(), "boom")
        assert redis.expiries["baselithcore:dlq:job:job-1"] == 1234

    def test_default_retention_is_seven_days(self, dlq, redis):
        with _config():
            dlq.record(_job(), "boom")
        assert redis.expiries["baselithcore:dlq:job:job-1"] == 7 * 24 * 3600

    def test_index_is_pruned_of_expired_entries(self, dlq, redis):
        with _config(dlq_retention_seconds=100):
            dlq.record(_job("old"), "boom")
            # Backdate the index entry past the horizon.
            redis.zsets["baselithcore:dlq:index"]["old"] = time.time() - 1000
            dlq.record(_job("fresh"), "boom")
        assert [r.job_id for r in dlq.list()] == ["fresh"]

    def test_zero_retention_keeps_records_forever(self, dlq, redis):
        with _config(dlq_retention_seconds=0):
            dlq.record(_job(), "boom")
        assert "baselithcore:dlq:job:job-1" not in redis.expiries
        assert dlq.count() == 1


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


class TestRedaction:
    def test_secret_kwargs_are_masked(self, dlq):
        with _config():
            dlq.record(
                _job(kwargs={"api_key": "sk-live-12345", "doc_id": "d1"}), "boom"
            )
        record = dlq.get("job-1")
        assert "sk-live-12345" not in record.kwargs_repr
        assert "sk-live-12345" not in record.kwargs_json
        assert "d1" in record.kwargs_json

    def test_inline_credentials_in_positional_args_are_masked(self, dlq):
        """Positional args are strings, so only the shared value-level rules
        of ``redact_sensitive`` apply (there is no key to match on)."""
        with _config():
            dlq.record(_job(args=("Bearer abc123", "api_key=sk-live-1")), "boom")
        record = dlq.get("job-1")
        assert "abc123" not in record.args_repr
        assert "abc123" not in record.args_json
        assert "sk-live-1" not in record.args_json

    def test_harmless_arguments_survive(self, dlq):
        with _config():
            dlq.record(_job(args=("doc-42", 7)), "boom")
        record = dlq.get("job-1")
        assert json.loads(record.args_json) == ["doc-42", 7]

    def test_pickled_payload_is_no_longer_stored(self, dlq):
        with _config():
            dlq.record(_job(), "boom")
        assert dlq.get("job-1").payload_b64 == ""

    def test_unserialisable_arguments_do_not_break_recording(self, dlq):
        with _config():
            dlq.record(_job(args=(object(),)), "boom")
        assert dlq.get("job-1") is not None

    def test_oversized_arguments_are_marked_unavailable_not_empty(self, dlq):
        """ "Too big to store" and "took no arguments" must not look alike."""
        with _config():
            dlq.record(_job(args=("x" * 10000,)), "boom")
        assert dlq.get("job-1").args_json == UNAVAILABLE_ARGS

    def test_redaction_failure_is_marked_unavailable(self, dlq, monkeypatch):
        monkeypatch.setattr(
            "core.task_queue.dead_letter.redact_sensitive",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("redaction down")),
        )
        with _config():
            dlq.record(_job(args=("secret-ish",)), "boom")
        record = dlq.get("job-1")
        assert record.args_json == UNAVAILABLE_ARGS
        assert "secret-ish" not in record.args_repr

    def test_a_genuinely_empty_call_stays_replayable(self, dlq):
        with _config():
            dlq.record(_job(args=(), kwargs={}), "boom")
        record = dlq.get("job-1")
        assert record.args_json == "[]" and record.kwargs_json == "{}"


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #


def _record(**overrides) -> DeadLetterRecord:
    base = {
        "job_id": "job-1",
        "func_name": "pkg.mod.fn",
        "origin_queue": "documents",
        "error": "boom",
        "traceback": "",
        "failed_at": 1.0,
        "tenant_id": "acme",
        "args_repr": "",
        "kwargs_repr": "",
        "args_json": '["a", 1]',
        "kwargs_json": '{"k": "v"}',
    }
    base.update(overrides)
    return DeadLetterRecord(**base)


@pytest.fixture(autouse=True)
def _allow_default_modules():
    """Most replay tests use `pkg.mod.fn`; widen the allowlist for them."""
    with _config(dlq_replay_allowed_modules=["pkg.", "core.", "plugins."]):
        yield


class TestReplay:
    def test_reenqueues_from_func_name_and_json(self, dlq):
        enqueued = {}

        class _Queue:
            def enqueue(self, func, *args, **kwargs):
                enqueued["func"] = func
                enqueued["args"] = args
                enqueued["kwargs"] = kwargs
                return types.SimpleNamespace(id="new-job")

        with patch("core.task_queue.dead_letter.get_queue", return_value=_Queue()):
            new_id = dlq._replay_from_record(_record())
        assert new_id == "new-job"
        assert enqueued["func"] == "pkg.mod.fn"
        assert enqueued["args"] == ("a", 1)
        assert enqueued["kwargs"]["k"] == "v"
        assert enqueued["kwargs"]["meta"]["replayed_from"] == "job-1"

    def test_never_unpickles_a_stored_payload(self, dlq):
        """``Job.restore`` on attacker-controlled bytes is remote code
        execution; the replay path must not reach for it at all."""
        with patch("rq.job.Job.restore", side_effect=AssertionError("unpickled!")):
            with patch(
                "core.task_queue.dead_letter.get_queue",
                return_value=types.SimpleNamespace(
                    enqueue=lambda *a, **k: types.SimpleNamespace(id="new-job")
                ),
            ):
                assert dlq._replay_from_record(_record()) == "new-job"

    def test_missing_func_name_is_refused(self, dlq):
        with pytest.raises(DeadLetterError, match="function reference"):
            dlq._replay_from_record(_record(func_name=""))

    def test_non_dotted_func_name_is_refused(self, dlq):
        with pytest.raises(DeadLetterError, match="function reference"):
            dlq._replay_from_record(_record(func_name="rm -rf /"))

    def test_malformed_args_json_is_refused(self, dlq):
        with pytest.raises(DeadLetterError, match="arguments"):
            dlq._replay_from_record(_record(args_json="{not json"))

    def test_args_json_that_is_not_a_list_is_refused(self, dlq):
        with pytest.raises(DeadLetterError, match="arguments"):
            dlq._replay_from_record(_record(args_json='{"a": 1}'))

    def test_kwargs_json_that_is_not_a_mapping_is_refused(self, dlq):
        with pytest.raises(DeadLetterError, match="arguments"):
            dlq._replay_from_record(_record(kwargs_json="[1, 2]"))

    def test_uncaptured_arguments_are_refused_not_replayed_as_empty(self, dlq):
        """The regression: an unstorable call replayed as ``func()``, silently
        dropping every argument it was supposed to carry."""
        with pytest.raises(DeadLetterError, match="no captured arguments"):
            dlq._replay_from_record(_record(args_json=UNAVAILABLE_ARGS))

    def test_uncaptured_kwargs_are_refused_too(self, dlq):
        with pytest.raises(DeadLetterError, match="no captured arguments"):
            dlq._replay_from_record(_record(kwargs_json=UNAVAILABLE_ARGS))

    def test_a_legacy_record_without_the_fields_is_refused(self, dlq, redis):
        """Records written before argument capture existed have arguments —
        just not ones we stored. Replaying them as ``func()`` is wrong."""
        redis.hashes["baselithcore:dlq:job:legacy"] = {
            "job_id": "legacy",
            "func_name": "core.jobs.reindex",
            "origin_queue": "documents",
            "error": "boom",
            "traceback": "",
            "failed_at": "1.0",
            "tenant_id": "acme",
            "args_repr": "('doc-42',)",
            "kwargs_repr": "{}",
        }
        record = dlq.get("legacy")
        assert record.args_json == UNAVAILABLE_ARGS
        with pytest.raises(DeadLetterError, match="no captured arguments"):
            dlq._replay_from_record(record)

    def test_a_genuinely_empty_call_replays(self, dlq):
        enqueued = {}

        class _Queue:
            def enqueue(self, func, *args, **kwargs):
                enqueued["args"] = args
                return types.SimpleNamespace(id="new-job")

        with patch("core.task_queue.dead_letter.get_queue", return_value=_Queue()):
            dlq._replay_from_record(_record(args_json="[]", kwargs_json="{}"))
        assert enqueued["args"] == ()


class TestReplayAllowlist:
    """A DLQ row is attacker-influenceable data, and RQ imports what it is
    handed — so a well-formed dotted path is not enough on its own."""

    def _queue(self, enqueued):
        class _Queue:
            def enqueue(self, func, *args, **kwargs):
                enqueued["func"] = func
                return types.SimpleNamespace(id="new-job")

        return _Queue()

    def test_an_allowed_module_replays(self, dlq):
        enqueued: dict = {}
        with (
            _config(dlq_replay_allowed_modules=["core.", "plugins."]),
            patch(
                "core.task_queue.dead_letter.get_queue",
                return_value=self._queue(enqueued),
            ),
        ):
            dlq._replay_from_record(_record(func_name="core.task_queue.jobs.reindex"))
        assert enqueued["func"] == "core.task_queue.jobs.reindex"

    def test_os_system_is_refused_before_any_import(self, dlq):
        with (
            _config(dlq_replay_allowed_modules=["core.", "plugins."]),
            patch(
                "core.task_queue.dead_letter.get_queue",
                side_effect=AssertionError("must not reach the queue"),
            ),
        ):
            with pytest.raises(DeadLetterError, match="ALLOWED_MODULES"):
                dlq._replay_from_record(_record(func_name="os.system"))

    def test_a_near_miss_prefix_is_refused(self, dlq):
        with _config(dlq_replay_allowed_modules=["core."]):
            with pytest.raises(DeadLetterError, match="ALLOWED_MODULES"):
                dlq._replay_from_record(_record(func_name="coreevil.jobs.run"))

    def test_a_custom_prefix_can_be_configured(self, dlq):
        enqueued: dict = {}
        with (
            _config(dlq_replay_allowed_modules=["myapp.tasks."]),
            patch(
                "core.task_queue.dead_letter.get_queue",
                return_value=self._queue(enqueued),
            ),
        ):
            dlq._replay_from_record(_record(func_name="myapp.tasks.rebuild"))
        assert enqueued["func"] == "myapp.tasks.rebuild"

    def test_an_empty_allowlist_refuses_everything(self, dlq):
        """Fail-closed: "nothing is allowed" means no replay, not a free pass."""
        with _config(dlq_replay_allowed_modules=[]):
            with pytest.raises(DeadLetterError, match="ALLOWED_MODULES"):
                dlq._replay_from_record(_record(func_name="core.jobs.run"))

    def test_the_default_allows_this_repository_s_own_task_modules(self, dlq):
        enqueued: dict = {}
        with (
            _config(),
            patch(
                "core.task_queue.dead_letter.get_queue",
                return_value=self._queue(enqueued),
            ),
        ):
            dlq._replay_from_record(_record(func_name="core.task_queue.jobs.run"))
            dlq._replay_from_record(_record(func_name="plugins.baselithbot.jobs.run"))
        assert enqueued["func"] == "plugins.baselithbot.jobs.run"

    def test_round_trip_from_a_recorded_job(self, dlq):
        enqueued = {}

        class _Queue:
            def enqueue(self, func, *args, **kwargs):
                enqueued["args"] = args
                return types.SimpleNamespace(id="new-job")

        with _config():
            dlq.record(_job(args=("doc-42",), kwargs={"mode": "full"}), "boom")
        record = dlq.get("job-1")
        with patch("core.task_queue.dead_letter.get_queue", return_value=_Queue()):
            dlq._replay_from_record(record)
        assert enqueued["args"] == ("doc-42",)
