"""Stable run ids for entry points that retry: chat requests and queued jobs.

Durable execution keys everything by ``run_id`` — the checkpoint, the
idempotency ledger, the run-event stream. An entry point that mints a fresh id
per attempt therefore defeats all of it: the chat service never passed one,
and an RQ retry of an agent-run job started a new run from scratch, re-paying
every LLM call and re-executing every effect the ledger could not match.

:func:`derive_run_id` turns an entry point's own retry identity (an
``Idempotency-Key`` header from an authenticated caller, an RQ job id) into a run id
scoped to the tenant and principal, and :func:`process_with_stable_run_id`
decides what an attempt under that id does:

* no checkpoint yet — a fresh run under the id;
* a ``completed`` run — its recorded answer, without executing anything (a
  retry after the response was lost);
* a ``running`` or ``failed`` run — ``resume=True``: recorded steps replay,
  the rest runs. A run whose lease another executor still holds raises
  :class:`~baselith.core.orchestration.checkpoint_errors.CheckpointLeaseHeldError`;
* a run paused ``awaiting_approval`` — the pause, unchanged (a decision goes
  through ``/approvals``, not a retry);
* a run recorded for a *different* query (a client reusing a request id) —
  left alone: the attempt runs fresh under a new id.
"""

from __future__ import annotations

import hashlib
from typing import Any

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    STATUS_COMPLETED,
)

logger = get_logger(__name__)

__all__ = ["derive_run_id", "process_with_stable_run_id"]


def derive_run_id(
    namespace: str,
    key: str,
    *,
    tenant_id: str | None = None,
    principal: str | None = None,
) -> str:
    """A run id for ``key`` that cannot collide across tenants or principals.

    Args:
        namespace: Entry point (``"chat"``, ``"agent-job"``); readable prefix.
        key: The entry point's retry identity.
        tenant_id: Owning tenant.
        principal: Calling user, so two users of one tenant reusing a header
            value never share a run (or each other's answers).

    Returns:
        ``<namespace>-<32 hex>``.
    """
    material = "\x1f".join((namespace, tenant_id or "", principal or "", key))
    return f"{namespace}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


async def process_with_stable_run_id(
    orchestrator: Any,
    query: str,
    context: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Run ``query`` as attempt N of the run ``run_id`` (see module docstring).

    Without a checkpoint store this is ``process(query, context,
    run_id=run_id)``: the id still names the run's event stream.

    Raises:
        CheckpointLeaseHeldError: Another live executor owns the run.
    """
    store = getattr(orchestrator, "checkpoint_store", None)
    existing = await store.load(run_id) if store is not None else None
    if existing is None:
        result: dict[str, Any] = await orchestrator.process(
            query, context, run_id=run_id
        )
        return result
    if existing.query != query:
        logger.warning(
            "stable_run_id_reused_for_other_query run=%s; running fresh", run_id
        )
        fresh: dict[str, Any] = await orchestrator.process(query, context)
        return fresh
    if existing.status == STATUS_COMPLETED:
        logger.info("stable_run_replayed run=%s", run_id)
        return {
            "response": existing.answer if existing.answer is not None else "",
            "intent": existing.intent,
            "run_id": run_id,
            "replayed": True,
        }
    if existing.status == STATUS_AWAITING_APPROVAL:
        pending = existing.pending_approval or {}
        return {
            "response": "This run is awaiting human approval.",
            "intent": existing.intent,
            "error": False,
            "awaiting_approval": True,
            "run_id": run_id,
            "pending_approval": {
                "tool_name": pending.get("tool_name"),
                "category": pending.get("category"),
            },
        }
    logger.info("stable_run_resumed run=%s status=%s", run_id, existing.status)
    resumed: dict[str, Any] = await orchestrator.process(
        query, context, run_id=run_id, resume=True
    )
    return resumed
