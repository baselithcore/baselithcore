"""Dead-letter queue (DLQ) for terminally-failed background jobs.

RQ keeps failed jobs in a per-queue ``FailedJobRegistry`` that expires after
``failure_ttl`` (7 days by default). That is fine for short-term inspection but
loses jobs afterwards and offers no first-class replay. This module adds a
durable DLQ:

- **Capture** — when a job exhausts its retries, the worker records it here with
  the failure context (error, traceback, origin queue, tenant, timestamp) and a
  **redacted, JSON-encoded** copy of the call arguments.
- **Inspect** — list/get/count failed jobs for dashboards and alerting.
- **Replay** — re-enqueue a dead-lettered job onto its original queue, either by
  requeuing the live RQ job or by rebuilding the call from ``func_name`` plus
  those JSON arguments.
- **Purge** — drop individual records, clear the DLQ, or let them expire.

Three properties are deliberate and easy to regress:

* **Nothing is pickled, and not everything is importable.** Records used to
  store the RQ payload and replay it through ``Job.restore``, which unpickles
  it. Anyone who can write to the queue's Redis could therefore choose the
  bytes a worker deserialises — remote code execution behind one ``replay()``
  click. Replay now rebuilds the call from a dotted function reference and
  JSON, with the parsed shapes validated before they reach ``enqueue`` and the
  reference checked against ``TASK_QUEUE_DLQ_REPLAY_ALLOWED_MODULES`` before
  anything is imported (``os.system`` is a perfectly well-formed dotted path).
  A record whose arguments could not be captured is refused outright rather
  than replayed as a no-argument call.
* **Arguments are redacted** through
  :func:`core.observability.redaction.redact_sensitive` before they are stored,
  so a job that took an API key does not leave it sitting in Redis under a key
  with no TTL. The trade-off is explicit: a replay re-runs with the *redacted*
  values, so a job whose argument really was a secret must be re-enqueued by
  its owner, not replayed.
* **Records expire.** ``TASK_QUEUE_DLQ_RETENTION_SECONDS`` (7 days by default)
  bounds the keyspace; the index is pruned of anything past the horizon on
  every write, so it cannot outlive the hashes it points at.

Storage (Redis):
  ``baselithcore:dlq:index``       sorted set  member=job_id, score=failed_at
  ``baselithcore:dlq:job:<id>``    hash        full record (see DeadLetterRecord)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from redis import Redis

from core.config import get_task_queue_config
from core.observability.logging import get_logger
from core.observability.redaction import redact_sensitive
from core.task_queue import get_queue, get_queue_redis_connection

logger = get_logger(__name__)

_PREFIX = "baselithcore:dlq:"
_INDEX_KEY = f"{_PREFIX}index"

#: A replayable function reference is a dotted import path, nothing else. RQ
#: will happily resolve whatever string it is handed, so this is the gate
#: between a stored record and an import.
_FUNC_REF_RE = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+$")

#: Cap on the stored argument encodings. A failed bulk job can carry megabytes
#: of payload, and the DLQ is a diagnostic record, not a copy of the input.
_MAX_ARGS_CHARS = 4000

#: Stored in ``args_json``/``kwargs_json`` when the real arguments could not be
#: captured (unserialisable, oversized, or redaction failed). It must be
#: distinguishable from ``"[]"``/``"{}"`` — those mean "the call genuinely took
#: no arguments", and conflating the two replays ``func()`` with everything
#: silently dropped. Records written before this field existed decode to the
#: same sentinel, for the same reason.
UNAVAILABLE_ARGS = ""


def _redact_call(args: Any, kwargs: Any) -> tuple[list[Any], dict[str, Any]] | None:
    """Return ``(args, kwargs)`` with secrets masked, or ``None`` on failure.

    Reuses the structlog redaction processor, so the DLQ and the logs agree on
    what counts as a secret instead of maintaining a second key list. ``None``
    means "could not be captured" and must not be confused with an empty call.
    """
    payload: dict[str, Any] = {"args": list(args or ()), "kwargs": dict(kwargs or {})}
    try:
        cleaned = dict(redact_sensitive(None, "", payload))
    except Exception as exc:  # redaction must never block a dead-letter write
        logger.debug("DLQ redaction failed, arguments not captured: %s", exc)
        return None
    safe_args = cleaned.get("args")
    safe_kwargs = cleaned.get("kwargs")
    if not isinstance(safe_args, (list, tuple)) or not isinstance(safe_kwargs, dict):
        return None
    return list(safe_args), dict(safe_kwargs)


def _encode_args(value: Any) -> str:
    """JSON-encode call arguments, bounded and never raising.

    Returns :data:`UNAVAILABLE_ARGS` when the value cannot be represented
    compactly; replay then refuses the record rather than guessing.
    """
    try:
        encoded = json.dumps(value, default=str)
    except Exception:  # silent-ok: unserialisable args ⇒ record stays, replay refuses
        return UNAVAILABLE_ARGS
    return encoded if len(encoded) <= _MAX_ARGS_CHARS else UNAVAILABLE_ARGS


def _replay_allowed_modules() -> tuple[str, ...]:
    """Module prefixes a dead-lettered job may be replayed from."""
    try:
        return tuple(get_task_queue_config().dlq_replay_allowed_modules)
    except Exception as exc:
        logger.debug("DLQ replay allowlist unavailable: %s", exc)
        return ()


def _job_key(job_id: str) -> str:
    return f"{_PREFIX}job:{job_id}"


class DeadLetterError(Exception):
    """Raised when a DLQ operation (e.g. replay) cannot be completed."""


def _decode_call(record: DeadLetterRecord) -> tuple[list[Any], dict[str, Any]]:
    """Parse and validate a record's stored JSON arguments.

    Raises:
        DeadLetterError: The stored JSON is malformed or has the wrong shape.
            Refusing is the point — these bytes come back out of Redis, and a
            replay must not splat an arbitrary structure into a call.
    """
    if record.args_json == UNAVAILABLE_ARGS or record.kwargs_json == UNAVAILABLE_ARGS:
        raise DeadLetterError(
            f"Job {record.job_id!r} has no captured arguments to replay "
            "(they were unserialisable, oversized, redacted away, or the "
            "record predates argument capture). Re-enqueue it from the code "
            "that owns the call instead."
        )
    try:
        args = json.loads(record.args_json)
        kwargs = json.loads(record.kwargs_json)
    except (TypeError, ValueError) as exc:
        raise DeadLetterError(
            f"Job {record.job_id!r} has unreadable stored arguments: {exc}"
        ) from exc
    if not isinstance(args, list):
        raise DeadLetterError(
            f"Job {record.job_id!r} has non-list positional arguments stored."
        )
    if not isinstance(kwargs, dict) or not all(isinstance(k, str) for k in kwargs):
        raise DeadLetterError(
            f"Job {record.job_id!r} has non-mapping keyword arguments stored."
        )
    return args, kwargs


@dataclass
class DeadLetterRecord:
    """Durable record of a terminally-failed job."""

    job_id: str
    func_name: str
    origin_queue: str
    error: str
    traceback: str
    failed_at: float
    tenant_id: str
    #: Redacted ``repr()`` of the call, for humans reading a dashboard.
    args_repr: str
    kwargs_repr: str
    #: Redacted JSON of the same call, for replay.
    #: :data:`UNAVAILABLE_ARGS` means "not captured" — distinct from ``"[]"``,
    #: which means the call really took no positional arguments.
    args_json: str = UNAVAILABLE_ARGS
    kwargs_json: str = UNAVAILABLE_ARGS
    #: Deprecated. Held the pickled RQ payload; no longer written, because
    #: replaying it meant unpickling bytes anyone with Redis write access could
    #: choose. Kept so records stored by an older version still load.
    payload_b64: str = ""

    def to_redis(self) -> dict[str, str]:
        """Serialize to a flat ``str -> str`` mapping for a Redis hash."""
        return {
            k: (v if isinstance(v, str) else json.dumps(v))
            for k, v in asdict(self).items()
        }

    @classmethod
    def from_redis(cls, data: dict[str, str]) -> DeadLetterRecord:
        """Rebuild from a Redis hash mapping."""
        return cls(
            job_id=data["job_id"],
            func_name=data.get("func_name", ""),
            origin_queue=data.get("origin_queue", "default"),
            error=data.get("error", ""),
            traceback=data.get("traceback", ""),
            failed_at=float(data.get("failed_at", "0") or 0),
            tenant_id=data.get("tenant_id", "default"),
            args_repr=data.get("args_repr", ""),
            kwargs_repr=data.get("kwargs_repr", ""),
            # A record written before these fields existed has no captured
            # arguments — not empty ones. Defaulting to "[]"/"{}" would replay
            # it as func() with every argument silently dropped.
            args_json=data.get("args_json", UNAVAILABLE_ARGS),
            kwargs_json=data.get("kwargs_json", UNAVAILABLE_ARGS),
            payload_b64=data.get("payload_b64", ""),
        )


class DeadLetterQueue:
    """Durable dead-letter store backed by Redis."""

    def __init__(self, connection: Redis | None = None) -> None:
        # Typed Any: the redis-py sync stubs union sync/async return types
        # (ResponseT), which is noise for this sync-only client.
        self._conn: Any = (
            connection if connection is not None else get_queue_redis_connection()
        )

    def record(
        self,
        job: Any,
        error: str,
        traceback_str: str = "",
    ) -> DeadLetterRecord:
        """Persist a failed job into the DLQ.

        Args:
            job: The RQ ``Job`` that failed terminally.
            error: Short error string (typically ``exc_value``).
            traceback_str: Full traceback text, if available.

        Returns:
            The stored :class:`DeadLetterRecord`.

        Notes:
            Arguments are redacted before storage, so the record can be shown
            in a dashboard and replayed without handing back whatever secret
            the original call carried.
        """
        redacted = _redact_call(getattr(job, "args", ()), getattr(job, "kwargs", {}))
        safe_args, safe_kwargs = redacted if redacted is not None else ([], {})
        record = DeadLetterRecord(
            job_id=job.id,
            func_name=getattr(job, "func_name", "") or "",
            origin_queue=getattr(job, "origin", "default") or "default",
            error=error,
            traceback=traceback_str,
            failed_at=time.time(),
            tenant_id=str((job.meta or {}).get("tenant_id", "default")),
            args_repr=(repr(tuple(safe_args))[:2000] if redacted else "<unavailable>"),
            kwargs_repr=(repr(safe_kwargs)[:2000] if redacted else "<unavailable>"),
            args_json=(
                _encode_args(safe_args) if redacted is not None else UNAVAILABLE_ARGS
            ),
            kwargs_json=(
                _encode_args(safe_kwargs) if redacted is not None else UNAVAILABLE_ARGS
            ),
        )
        retention = self._retention_seconds()
        pipe = self._conn.pipeline()
        pipe.hset(_job_key(record.job_id), mapping=record.to_redis())
        pipe.zadd(_INDEX_KEY, {record.job_id: record.failed_at})
        if retention > 0:
            pipe.expire(_job_key(record.job_id), retention)
            # The hashes expire on their own, but the index does not: prune it
            # here or `list()` keeps returning ids whose record is long gone.
            pipe.zremrangebyscore(_INDEX_KEY, 0, record.failed_at - retention)
        pipe.execute()
        logger.warning(
            "Dead-lettered job %s (%s) from queue %s: %s",
            record.job_id,
            record.func_name,
            record.origin_queue,
            error,
        )
        return record

    @staticmethod
    def _retention_seconds() -> int:
        """Configured DLQ horizon in seconds; 0 means keep forever."""
        try:
            return max(0, int(get_task_queue_config().dlq_retention_seconds))
        except Exception as exc:  # configuration must not block a DLQ write
            logger.debug("DLQ retention unavailable, keeping record: %s", exc)
            return 0

    def count(self) -> int:
        """Number of jobs currently in the DLQ."""
        return int(self._conn.zcard(_INDEX_KEY))

    def list(self, limit: int = 50, offset: int = 0) -> list[DeadLetterRecord]:
        """Return DLQ records, most-recently-failed first."""
        start = offset
        end = offset + limit - 1
        ids = self._conn.zrevrange(_INDEX_KEY, start, end)
        if not ids:
            return []

        # Fetch every record's hash in one round-trip instead of one hgetall
        # per id (N+1). Order is preserved by zip with the id list.
        pipe = self._conn.pipeline()
        for raw_id in ids:
            job_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            pipe.hgetall(_job_key(job_id))
        hashes = pipe.execute()

        records: list[DeadLetterRecord] = []
        for data in hashes:
            if not data:
                continue
            decoded = {
                (k.decode() if isinstance(k, bytes) else k): (
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in data.items()
            }
            records.append(DeadLetterRecord.from_redis(decoded))
        return records

    def get(self, job_id: str) -> DeadLetterRecord | None:
        """Fetch a single DLQ record, or ``None`` if absent."""
        data = self._conn.hgetall(_job_key(job_id))
        if not data:
            return None
        decoded = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in data.items()
        }
        return DeadLetterRecord.from_redis(decoded)

    def replay(self, job_id: str, *, purge: bool = True) -> str:
        """Re-enqueue a dead-lettered job onto its original queue.

        Tries to requeue the live RQ job first; once that has expired, rebuilds
        the call from the record's ``func_name`` and stored JSON arguments.

        Args:
            job_id: The dead-lettered job id.
            purge: Remove the DLQ record after a successful replay.

        Returns:
            The id of the re-enqueued job.

        Raises:
            DeadLetterError: If the record is missing or cannot be replayed.

        Notes:
            The rebuilt call uses the *redacted* arguments (see the module
            docstring). A job whose argument genuinely was a secret cannot be
            replayed faithfully and must be re-enqueued by its owner.
        """
        record = self.get(job_id)
        if record is None:
            raise DeadLetterError(f"No dead-letter record for job {job_id!r}.")

        from rq.job import Job

        new_id: str
        try:
            job = Job.fetch(job_id, connection=self._conn)
            job.requeue()
            new_id = job.id
        except Exception:
            new_id = self._replay_from_record(record)

        if purge:
            self.purge(job_id)
        logger.info("Replayed dead-lettered job %s -> %s", job_id, new_id)
        return new_id

    def _replay_from_record(self, record: DeadLetterRecord) -> str:
        """Rebuild and enqueue a job from its stored reference and arguments.

        Deliberately does not touch ``Job.restore``: that unpickles a blob read
        straight out of Redis, which turns "replay a failed job" into arbitrary
        code execution for anyone who can write to the queue database.

        Nor is a well-formed dotted path enough. ``os.system`` is a well-formed
        dotted path, and RQ will import whatever it is handed — so the
        reference must also fall under one of the configured module prefixes
        (``TASK_QUEUE_DLQ_REPLAY_ALLOWED_MODULES``), checked *before* anything
        is imported. Arguments are then parsed as JSON and shape-checked.
        """
        func_ref = (record.func_name or "").strip()
        if not _FUNC_REF_RE.match(func_ref):
            raise DeadLetterError(
                f"Job {record.job_id!r} has no usable function reference to "
                f"replay (got {record.func_name!r}; expected a dotted import "
                "path)."
            )
        allowed = _replay_allowed_modules()
        if not func_ref.startswith(allowed):
            raise DeadLetterError(
                f"Job {record.job_id!r} names {func_ref!r}, which is outside "
                f"TASK_QUEUE_DLQ_REPLAY_ALLOWED_MODULES ({list(allowed)}); "
                "refusing to import it. Add its module prefix to that setting "
                "if this really is one of your task modules."
            )
        args, kwargs = _decode_call(record)
        queue = get_queue(record.origin_queue)
        enqueued = queue.enqueue(
            func_ref,
            *args,
            **kwargs,
            meta={"tenant_id": record.tenant_id, "replayed_from": record.job_id},
        )
        # RQ ships no py.typed, so `enqueued.id` is `Any`.
        return str(enqueued.id)

    def purge(self, job_id: str) -> bool:
        """Remove a single record from the DLQ."""
        pipe = self._conn.pipeline()
        pipe.delete(_job_key(job_id))
        pipe.zrem(_INDEX_KEY, job_id)
        results = pipe.execute()
        return bool(results[0])

    def purge_all(self) -> int:
        """Clear the entire DLQ. Returns the number of records removed."""
        ids = self._conn.zrange(_INDEX_KEY, 0, -1)
        pipe = self._conn.pipeline()
        for raw_id in ids:
            job_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            pipe.delete(_job_key(job_id))
        pipe.delete(_INDEX_KEY)
        pipe.execute()
        return len(ids)


_dlq: DeadLetterQueue | None = None


def get_dead_letter_queue() -> DeadLetterQueue:
    """Return the process-wide DLQ instance."""
    global _dlq
    if _dlq is None:
        _dlq = DeadLetterQueue()
    return _dlq


def dead_letter_handler(job: Any, exc_type: Any, exc_value: Any, tb: Any) -> bool:
    """RQ exception handler that records terminally-failed jobs to the DLQ.

    Records only when the job has no retries left, so transient failures that
    RQ will retry are not dead-lettered prematurely. Always returns ``True`` so
    RQ's default handling (move to FailedJobRegistry) still runs.
    """
    try:
        retries_left = getattr(job, "retries_left", None)
        if retries_left in (None, 0):
            import traceback as _tb

            tb_text = "".join(_tb.format_exception(exc_type, exc_value, tb))
            get_dead_letter_queue().record(job, str(exc_value), tb_text)
    except Exception as exc:
        logger.error(
            "Dead-letter handler failed for job %s: %s", getattr(job, "id", "?"), exc
        )
    return True


__all__ = [
    "UNAVAILABLE_ARGS",
    "DeadLetterError",
    "DeadLetterQueue",
    "DeadLetterRecord",
    "dead_letter_handler",
    "get_dead_letter_queue",
]
