"""Stable run ids for streamed runs: replay a finished answer, resume the rest.

The streaming twin of :func:`~baselith.core.orchestration.run_ids.process_with_stable_run_id`.
An entry point that names a streamed run (``POST /chat/stream`` derives the id
from an authenticated caller's ``Idempotency-Key``) opens it here, and gets
back the chunk source together with what kind of attempt it is — the SSE layer
needs to know whether the events it is about to send are the ones a
reconnecting client already partly received:

* no checkpoint yet — :data:`MODE_FRESH`: a new streamed run under the id;
* a ``completed`` run — :data:`MODE_REPLAYED`: the recorded answer, re-emitted
  in the chunks it was delivered in, with nothing executed. Event ``n`` of the
  replay is event ``n`` of the original stream, so a client resuming with
  ``Last-Event-ID: n`` can be sent only what follows;
* a ``running`` or ``failed`` run whose lease has expired —
  :data:`MODE_RESUMED`: ``process_stream(resume=True)``. Recorded steps
  replay; the answer text is generated again, because a token stream cannot
  be continued mid-generation (see :mod:`baselith.core.orchestration.mixins._streaming`);
* a run whose lease another executor still holds —
  :class:`~baselith.core.orchestration.checkpoint_errors.CheckpointLeaseHeldError`,
  raised **before** any byte is streamed, so an HTTP entry point can still
  answer 409;
* a run paused ``awaiting_approval`` — :data:`MODE_PAUSED`: the pause notice;
* a run recorded for a *different* query — a fresh run under a new id.

A run completed by the non-streaming path (the recovery sweep resumes an
interrupted stream through ``process()``) has an answer but no chunk record:
it replays as one chunk, through the output guard.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    STATUS_COMPLETED,
    Checkpoint,
)
from baselith.core.orchestration.checkpoint_errors import CheckpointLeaseHeldError
from baselith.core.orchestration.checkpoint_lease import lease_is_live
from baselith.core.orchestration.mixins._streaming import STREAM_CHUNKS_KEY

logger = get_logger(__name__)

__all__ = [
    "MODE_FRESH",
    "MODE_PAUSED",
    "MODE_REPLAYED",
    "MODE_RESUMED",
    "StreamRun",
    "open_stream_run",
    "replay_chunks",
]

MODE_FRESH = "fresh"
MODE_RESUMED = "resumed"
MODE_REPLAYED = "replayed"
MODE_PAUSED = "paused"

#: What a stream says for a run paused on a human decision.
PAUSED_MESSAGE = "This run is awaiting human approval."


@dataclass
class StreamRun:
    """An opened streamed run.

    Attributes:
        run_id: The run the chunks belong to (``None`` when no stable id
            applies, e.g. a reused id with a different query).
        mode: One of the ``MODE_*`` constants (see module docstring).
        source: The chunk stream.
    """

    run_id: str | None
    mode: str
    source: AsyncGenerator[str, None]


def replay_chunks(checkpoint: Checkpoint) -> list[str] | None:
    """The completed stream's chunks, rebuilt from the recorded boundaries.

    Returns:
        The chunks, or ``None`` when the run recorded no (consistent) chunk
        boundaries — a run completed by the non-streaming path.
    """
    answer = checkpoint.answer if isinstance(checkpoint.answer, str) else None
    lengths = checkpoint.plugin_data.get(STREAM_CHUNKS_KEY)
    if answer is None or not isinstance(lengths, list):
        return None
    if not all(isinstance(n, int) and n >= 0 for n in lengths):
        return None
    if sum(lengths) != len(answer):
        return None
    chunks: list[str] = []
    offset = 0
    for length in lengths:
        chunks.append(answer[offset : offset + length])
        offset += length
    return chunks


async def _replay(checkpoint: Checkpoint) -> AsyncGenerator[str, None]:
    chunks = replay_chunks(checkpoint)
    if chunks is not None:
        # Already guarded: these are the chunks the client was sent.
        for chunk in chunks:
            yield chunk
        return
    from baselith.core.orchestration.guard_pipeline import guard_output_async

    answer = checkpoint.answer
    guarded = await guard_output_async(
        {"response": answer if isinstance(answer, str) else str(answer or "")}
    )
    text = guarded.get("response", "")
    if text:
        yield str(text)


async def _single(text: str) -> AsyncGenerator[str, None]:
    yield text


async def open_stream_run(
    orchestrator: Any,
    query: str,
    context: dict[str, Any],
    run_id: str,
) -> StreamRun:
    """Open attempt N of the streamed run ``run_id`` (see module docstring).

    Without a checkpoint store this is a fresh ``process_stream`` under the
    id.

    Raises:
        CheckpointLeaseHeldError: Another live executor owns the run.
    """
    store = getattr(orchestrator, "checkpoint_store", None)
    existing = await store.load(run_id) if store is not None else None
    if existing is None:
        return StreamRun(
            run_id,
            MODE_FRESH,
            orchestrator.process_stream(query, context, run_id=run_id),
        )
    if existing.query != query:
        logger.warning(
            "stable_stream_id_reused_for_other_query run=%s; running fresh", run_id
        )
        return StreamRun(None, MODE_FRESH, orchestrator.process_stream(query, context))
    if existing.status == STATUS_COMPLETED:
        logger.info("stable_stream_replayed run=%s", run_id)
        return StreamRun(run_id, MODE_REPLAYED, _replay(existing))
    if existing.status == STATUS_AWAITING_APPROVAL:
        return StreamRun(run_id, MODE_PAUSED, _single(PAUSED_MESSAGE))
    if lease_is_live(existing):
        raise CheckpointLeaseHeldError(
            run_id, existing.owner, existing.lease_expires_at
        )
    logger.info("stable_stream_resumed run=%s status=%s", run_id, existing.status)
    return StreamRun(
        run_id,
        MODE_RESUMED,
        orchestrator.process_stream(query, context, run_id=run_id, resume=True),
    )
