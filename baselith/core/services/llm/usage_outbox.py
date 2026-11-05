"""Hand billed LLM usage from a queue worker to the process that runs sinks.

A usage sink (:func:`~baselith.core.services.llm.register_usage_sink`) observes
every billed turn, but only in the process that registered it. A queue worker
hosts no plugins, so a plugin's sink never runs there: whatever a job spends
(a plugin calling the funnel from its job, a model measured outside it and
reported with :func:`~baselith.core.services.llm.report_external_usage`) was
invisible to every per-plugin ledger, and a plugin whose AI work all happens in
jobs showed up as having spent nothing.

The outbox closes that gap in two halves, both bounded and best-effort:

* **The worker side.** :func:`collect_job_usage` wraps a job. The work horse
  that runs it collects every billed turn, with the tenant, user and plugin the
  job was enqueued under, and pushes them to a Redis list when the job ends,
  before the work horse exits (it leaves by ``os._exit``, so anything held only
  in memory would be lost). The list is capped in length and in age.
* **The API side.** :func:`deliver_worker_usage` drains the list and re-emits
  each turn through the ordinary sink seam with that identity bound, so a sink
  sees it exactly as if the call had been made in-process. Whoever runs sinks
  calls it on a timer; Redis makes the drain atomic, so several API processes
  share the list and each turn is delivered once.

Nothing here prices or interprets usage: the record is the four-bucket
:class:`~baselith.core.services.llm.usage.Usage` the funnel metered, untouched.
A failure at either end is logged and swallowed: usage is an observation, and
a Redis hiccup must never fail a job or a request.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any

from baselith.core.context import (
    get_current_plugin,
    get_current_user_id,
    reset_plugin_context,
    reset_tenant_context,
    reset_user_context,
    set_plugin_context,
    set_tenant_context,
    set_user_context,
)
from baselith.core.observability.logging import get_logger
from baselith.core.services.llm.usage import Usage
from baselith.core.services.llm.usage_sinks import (
    UsageReport,
    emit_usage_report,
    register_usage_sink,
)

logger = get_logger(__name__)

#: Switch for the worker side; on by default. ``false``, ``0``, ``no`` or ``off`` disables it.
ENV_SWITCH = "BASELITH_WORKER_USAGE_OUTBOX"
OUTBOX_KEY = "baselith:llm:usage_outbox"
#: Oldest records are dropped beyond this many, so an absent consumer cannot grow Redis.
OUTBOX_MAX_RECORDS = 100_000
#: A list nobody drained for this long expires whole.
OUTBOX_TTL_SECONDS = 14 * 86_400
#: Records one delivery pass takes.
DRAIN_BATCH = 1_000

_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """One billed turn and the identity it was spent under."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated: bool = False
    batch: bool = False
    requests: int = 1
    tenant_id: str | None = None
    user_id: str | None = None
    plugin: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | bytes) -> OutboxRecord | None:
        """The record in ``raw``, or ``None`` when it is not one."""
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        model = data.get("model")
        if not isinstance(model, str) or not model:
            return None

        def number(key: str, default: int = 0) -> int:
            value = data.get(key, default)
            return (
                value
                if isinstance(value, int) and not isinstance(value, bool)
                else default
            )

        def text(key: str) -> str | None:
            value = data.get(key)
            return value if isinstance(value, str) and value else None

        return cls(
            model=model,
            input_tokens=number("input_tokens"),
            output_tokens=number("output_tokens"),
            cache_read_tokens=number("cache_read_tokens"),
            cache_write_tokens=number("cache_write_tokens"),
            estimated=data.get("estimated") is True,
            batch=data.get("batch") is True,
            requests=max(1, number("requests", 1)),
            tenant_id=text("tenant_id"),
            user_id=text("user_id"),
            plugin=text("plugin"),
        )

    def to_report(self) -> UsageReport:
        return UsageReport(
            model=self.model,
            usage=Usage(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cache_read_tokens=self.cache_read_tokens,
                cache_write_tokens=self.cache_write_tokens,
                estimated=self.estimated,
            ),
            batch=self.batch,
            requests=self.requests,
            tenant_id=self.tenant_id,
        )


def outbox_enabled() -> bool:
    """Whether a worker hands its usage off (``BASELITH_WORKER_USAGE_OUTBOX``)."""
    return os.getenv(ENV_SWITCH, "true").strip().lower() not in _FALSE_TOKENS


# The records of the job running in this context, ``None`` outside a job. A
# context variable rather than a global: the work horse runs one job at a time,
# but tasks and threads the job starts inherit it, and nothing else sees it.
_job_usage: ContextVar[list[OutboxRecord] | None] = ContextVar(
    "baselith_job_usage", default=None
)


def _collect(report: UsageReport) -> None:
    """Usage sink: note the turn when a job is collecting, otherwise do nothing."""
    bucket = _job_usage.get()
    if bucket is None:
        return
    usage = report.usage
    bucket.append(
        OutboxRecord(
            model=report.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            estimated=usage.estimated,
            batch=report.batch,
            requests=report.requests,
            tenant_id=report.tenant_id,
            user_id=get_current_user_id() or None,
            plugin=get_current_plugin() or None,
        )
    )


def push_records(connection: Any, records: list[OutboxRecord]) -> int:
    """Append ``records`` to the outbox, trimming it to its bounds. Never raises.

    Returns:
        How many records were pushed (0 after a logged failure).
    """
    if not records:
        return 0
    try:
        with connection.pipeline(transaction=True) as pipe:
            pipe.rpush(OUTBOX_KEY, *(record.to_json() for record in records))
            pipe.ltrim(OUTBOX_KEY, -OUTBOX_MAX_RECORDS, -1)
            pipe.expire(OUTBOX_KEY, OUTBOX_TTL_SECONDS)
            pipe.execute()
    except Exception as exc:  # usage is an observation, never a job failure
        logger.warning(
            "worker usage outbox push failed: %s (%d record(s) lost)",
            type(exc).__name__,
            len(records),
        )
        return 0
    return len(records)


@contextmanager
def collect_job_usage(connection: Any) -> Iterator[list[OutboxRecord]]:
    """Collect what the wrapped job bills and push it to the outbox on exit.

    Used around a job in the work horse that runs it. The push happens in a
    ``finally``: a job that raised still spent what it spent.

    Args:
        connection: A Redis client (the worker's own connection); ``None``
            collects nothing.
    """
    if connection is None or not outbox_enabled():
        yield []
        return
    register_usage_sink(_collect)  # idempotent
    bucket: list[OutboxRecord] = []
    token = _job_usage.set(bucket)
    try:
        yield bucket
    finally:
        _job_usage.reset(token)
        push_records(connection, bucket)


def drain_records(connection: Any, limit: int = DRAIN_BATCH) -> list[OutboxRecord]:
    """Take up to ``limit`` records off the outbox, oldest first.

    The read and the trim are one transaction, so concurrent consumers never
    take the same record. An entry that is not a record is dropped.
    """
    with connection.pipeline(transaction=True) as pipe:
        pipe.lrange(OUTBOX_KEY, 0, limit - 1)
        pipe.ltrim(OUTBOX_KEY, limit, -1)
        taken, _ = pipe.execute()
    records = (OutboxRecord.from_json(raw) for raw in taken or [])
    return [record for record in records if record is not None]


def _default_connection() -> Any:
    from redis import Redis

    from baselith.core.config import get_task_queue_config

    config = get_task_queue_config()
    return Redis.from_url(
        config.get_redis_url(),
        socket_connect_timeout=config.socket_connect_timeout,
        socket_timeout=config.socket_connect_timeout,
    )


def _redeliver(record: OutboxRecord) -> None:
    """Emit one turn through the sink seam under the identity it was spent under."""
    undo: list[Any] = []
    try:
        if record.tenant_id:
            token = set_tenant_context(record.tenant_id)
            undo.append(lambda token=token: reset_tenant_context(token))
        if record.user_id:
            token = set_user_context(record.user_id)
            undo.append(lambda token=token: reset_user_context(token))
        if record.plugin:
            token = set_plugin_context(record.plugin)
            undo.append(lambda token=token: reset_plugin_context(token))
        emit_usage_report(record.to_report())
    finally:
        for reset in reversed(undo):
            reset()


def deliver_worker_usage(
    connection: Any | None = None, limit: int = DRAIN_BATCH
) -> int:
    """Re-emit what workers billed to the usage sinks of this process.

    Meant to run on a timer in a process that has sinks (the one hosting the
    dashboard's ledger). Never raises.

    Args:
        connection: A Redis client; the task-queue Redis by default.
        limit: Records to take in this pass.

    Returns:
        How many records were delivered.
    """
    try:
        client = connection if connection is not None else _default_connection()
        try:
            records = drain_records(client, limit)
        finally:
            if connection is None:
                client.close()
    except Exception as exc:  # no Redis, no queue: nothing to deliver
        logger.debug("worker usage delivery skipped: %s", type(exc).__name__)
        return 0
    for record in records:
        _redeliver(record)
    return len(records)


__all__ = [
    "DRAIN_BATCH",
    "ENV_SWITCH",
    "OUTBOX_KEY",
    "OUTBOX_MAX_RECORDS",
    "OUTBOX_TTL_SECONDS",
    "OutboxRecord",
    "collect_job_usage",
    "deliver_worker_usage",
    "drain_records",
    "outbox_enabled",
    "push_records",
]
