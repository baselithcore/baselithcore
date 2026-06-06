"""Dead-letter queue (DLQ) for terminally-failed background jobs.

RQ keeps failed jobs in a per-queue ``FailedJobRegistry`` that expires after
``failure_ttl`` (7 days by default). That is fine for short-term inspection but
loses jobs afterwards and offers no first-class replay. This module adds a
durable DLQ:

- **Capture** — when a job exhausts its retries, the worker records it here with
  full failure context (error, traceback, origin queue, tenant, timestamp) plus
  the serialized RQ payload so it can be replayed even after the RQ job expires.
- **Inspect** — list/get/count failed jobs for dashboards and alerting.
- **Replay** — re-enqueue a dead-lettered job onto its original queue, either by
  requeuing the live RQ job or by reconstructing it from the stored payload.
- **Purge** — drop individual records or clear the DLQ.

Storage (Redis):
  ``baselithcore:dlq:index``       sorted set  member=job_id, score=failed_at
  ``baselithcore:dlq:job:<id>``    hash        full record (see _Record fields)
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

from redis import Redis

from core.observability.logging import get_logger
from core.task_queue import get_queue, get_queue_redis_connection

logger = get_logger(__name__)

_PREFIX = "baselithcore:dlq:"
_INDEX_KEY = f"{_PREFIX}index"


def _job_key(job_id: str) -> str:
    return f"{_PREFIX}job:{job_id}"


class DeadLetterError(Exception):
    """Raised when a DLQ operation (e.g. replay) cannot be completed."""


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
    args_repr: str
    kwargs_repr: str
    #: base64 of the RQ job's serialized payload, for replay after expiry.
    payload_b64: str = ""

    def to_redis(self) -> dict[str, str]:
        """Serialize to a flat ``str -> str`` mapping for a Redis hash."""
        return {
            k: (v if isinstance(v, str) else json.dumps(v))
            for k, v in asdict(self).items()
        }

    @classmethod
    def from_redis(cls, data: dict[str, str]) -> "DeadLetterRecord":
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
            payload_b64=data.get("payload_b64", ""),
        )


class DeadLetterQueue:
    """Durable dead-letter store backed by Redis."""

    def __init__(self, connection: Optional[Redis] = None) -> None:
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
        """
        payload_b64 = ""
        try:
            payload_b64 = base64.b64encode(job.data).decode("ascii")
        except Exception as exc:  # noqa: BLE001 — payload is best-effort
            logger.debug("Could not serialize job %s payload: %s", job.id, exc)

        record = DeadLetterRecord(
            job_id=job.id,
            func_name=getattr(job, "func_name", "") or "",
            origin_queue=getattr(job, "origin", "default") or "default",
            error=error,
            traceback=traceback_str,
            failed_at=time.time(),
            tenant_id=str((job.meta or {}).get("tenant_id", "default")),
            args_repr=repr(getattr(job, "args", ()))[:2000],
            kwargs_repr=repr(getattr(job, "kwargs", {}))[:2000],
            payload_b64=payload_b64,
        )
        pipe = self._conn.pipeline()
        pipe.hset(_job_key(record.job_id), mapping=record.to_redis())
        pipe.zadd(_INDEX_KEY, {record.job_id: record.failed_at})
        pipe.execute()
        logger.warning(
            "Dead-lettered job %s (%s) from queue %s: %s",
            record.job_id,
            record.func_name,
            record.origin_queue,
            error,
        )
        return record

    def count(self) -> int:
        """Number of jobs currently in the DLQ."""
        return int(self._conn.zcard(_INDEX_KEY))

    def list(self, limit: int = 50, offset: int = 0) -> list[DeadLetterRecord]:
        """Return DLQ records, most-recently-failed first."""
        start = offset
        end = offset + limit - 1
        ids = self._conn.zrevrange(_INDEX_KEY, start, end)
        records: list[DeadLetterRecord] = []
        for raw_id in ids:
            job_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            rec = self.get(job_id)
            if rec is not None:
                records.append(rec)
        return records

    def get(self, job_id: str) -> Optional[DeadLetterRecord]:
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

        Tries to requeue the live RQ job first; if it has expired, reconstructs
        it from the stored serialized payload.

        Args:
            job_id: The dead-lettered job id.
            purge: Remove the DLQ record after a successful replay.

        Returns:
            The id of the re-enqueued job.

        Raises:
            DeadLetterError: If the record is missing or cannot be replayed.
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
        except Exception:  # noqa: BLE001 — fall back to payload reconstruction
            new_id = self._replay_from_payload(record)

        if purge:
            self.purge(job_id)
        logger.info("Replayed dead-lettered job %s -> %s", job_id, new_id)
        return new_id

    def _replay_from_payload(self, record: DeadLetterRecord) -> str:
        """Reconstruct and enqueue a job from its stored serialized payload."""
        if not record.payload_b64:
            raise DeadLetterError(
                f"Job {record.job_id!r} has no stored payload to replay."
            )
        from rq.job import Job

        try:
            data = base64.b64decode(record.payload_b64)
            restored = Job(id=record.job_id, connection=self._conn)
            restored.restore(data)
        except Exception as exc:  # noqa: BLE001
            raise DeadLetterError(
                f"Could not reconstruct job {record.job_id!r}: {exc}"
            ) from exc
        func_ref: str = restored.func_name or record.func_name
        if not func_ref:
            raise DeadLetterError(
                f"Job {record.job_id!r} has no function reference to replay."
            )
        queue = get_queue(record.origin_queue)
        enqueued = queue.enqueue(
            func_ref,
            *restored.args,
            **restored.kwargs,
            meta={"tenant_id": record.tenant_id, "replayed_from": record.job_id},
        )
        return enqueued.id

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


_dlq: Optional[DeadLetterQueue] = None


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
    except Exception as exc:  # noqa: BLE001 — never break worker error handling
        logger.error(
            "Dead-letter handler failed for job %s: %s", getattr(job, "id", "?"), exc
        )
    return True


__all__ = [
    "DeadLetterQueue",
    "DeadLetterRecord",
    "DeadLetterError",
    "get_dead_letter_queue",
    "dead_letter_handler",
]
