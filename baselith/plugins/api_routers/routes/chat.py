"""
Chat Router.

Provides endpoints for synchronous and streaming chat interactions with the agent.
Integrates rate limiting and observability.

``POST /chat/stream`` speaks Server-Sent Events: one ``data:`` frame per model
chunk and a terminal ``event: done``. It used to answer ``text/plain`` with the
tokens simply concatenated, which gave a client no frame boundaries and no way
to tell a finished stream from a dropped connection. Two more things were
missing and are enforced here: the generator stops as soon as the client is
gone (a closed tab kept the upstream LLM call running and billing), and the
whole response is bounded by ``CHAT_STREAM_TIMEOUT_SECONDS``.

Every event carries a monotonically increasing ``id:`` (per response, from
``1``) and a quiet stream sends a ``: keepalive`` comment every
``SSE_HEARTBEAT_SECONDS``.

**Resuming a stream.** A client that lost the connection re-sends the request.
From an authenticated caller with the same ``Idempotency-Key`` it names the
same durable run (see :func:`chat_run_id`), and what comes back depends on
where that run is:

* **completed** — the recorded answer, replayed in the chunks it was first
  sent in and without running anything. ``Last-Event-ID: n`` skips the events
  the client already holds: the response continues at event ``n + 1``;
* **interrupted** (crash, shutdown, disconnect) — the run resumes: every
  recorded step (model turns, tool calls) replays, and the answer is
  generated again, since a token stream cannot be continued mid-generation.
  When the client sent ``Last-Event-ID``, the stream opens with an
  ``event: reset`` frame (no ``id:``) telling it to discard the partial text
  it holds; events then count from ``1`` again;
* **still running elsewhere** — **409**, before any byte is streamed.

Without a key, or from an anonymous caller, every request is a fresh run and
a ``Last-Event-ID`` only earns the ``reset`` frame.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, AsyncIterator

import orjson
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from baselith.core.chat import chat_service
from baselith.core.config import get_app_config
from baselith.core.middleware import require_user
from baselith.core.models.chat import ChatRequest, ChatResponse
from baselith.core.observability.logging import get_logger
from baselith.core.observability.setup import request_id_ctx
from baselith.core.transparency import TransparencyService, get_transparency_service
from baselith.plugins.api_routers.routes._sse import (
    DEFAULT_HEARTBEAT_SECONDS,
    HEARTBEAT,
    KEEPALIVE_FRAME,
    HeartbeatSource,
    heartbeat_seconds,
)
from baselith.plugins.api_routers.schemas import problem_responses

logger = get_logger(__name__)

# Header signalling that an AI-interaction disclosure applies (Art 50(1)). A
# fixed token keeps the value header-safe regardless of the configured text;
# the full notice rides in the response body metadata.
_DISCLOSURE_HEADER = "X-Baselith-AI-Disclosure"

# Hard cap on streamed response size in bytes (~4MB). Protects against
# unbounded memory growth from runaway LLM generations. Public names: the
# WebSocket surface (chat_ws) applies the same guards.
STREAM_MAX_BYTES = 4 * 1024 * 1024
# Hard cap per-chunk size to prevent single oversized chunk DoS.
STREAM_MAX_CHUNK_BYTES = 64 * 1024

# Terminal SSE frame. Clients watch for it to tell "the model finished" apart
# from "the connection died"; ``[DONE]`` matches the convention every OpenAI-
# compatible SSE client already implements.
SSE_DONE_EVENT = "event: done\ndata: [DONE]\n\n"

# In-band failure notice. Response headers are long gone by the time a provider
# read fails mid-stream, so a 200 that simply stops is indistinguishable from a
# dropped socket. The payload is a fixed JSON object — a stable ``code``, the
# fixed ``detail`` and the ``request_id`` an operator greps the log for: the
# exception text can carry provider detail, prompt fragments or credentials,
# and it is already in the log, keyed by that request id.
SSE_ERROR_CODE = "stream_failed"
SSE_ERROR_DETAIL = "stream failed"


# Opens a stream whose earlier partial text (held by a reconnecting client)
# will not be continued: the client discards it and renders what follows.
SSE_RESET_EVENT = 'event: reset\ndata: {"reason":"regenerated"}\n\n'


def last_event_id(request: Request) -> int:
    """The ``Last-Event-ID`` a reconnecting client sent, ``0`` when none/invalid."""
    raw = request.headers.get("last-event-id", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


def sse_error_payload(request_id: str | None = None) -> str:
    """The JSON ``data:`` of the in-band ``event: error`` frame."""
    rid = request_id if request_id is not None else request_id_ctx.get(None)
    return orjson.dumps(
        {"code": SSE_ERROR_CODE, "detail": SSE_ERROR_DETAIL, "request_id": rid}
    ).decode()


def sse_error_event(event_id: int | None = None, request_id: str | None = None) -> str:
    """Render the in-band ``event: error`` frame."""
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{id_line}event: error\ndata: {sse_error_payload(request_id)}\n\n"


# Fallback when the app config predates CHAT_STREAM_TIMEOUT_SECONDS (legacy
# test doubles stub the config with a partial namespace).
DEFAULT_STREAM_TIMEOUT_SECONDS = 300.0


class SSEResponse(StreamingResponse):
    """A ``StreamingResponse`` that also tells OpenAPI what it sends.

    FastAPI derives a route's documented 200 content type from its
    ``response_class``; a bare ``StreamingResponse`` has ``media_type = None``,
    so the generated schema advertised ``application/json`` for a route that
    only ever speaks SSE — and every SDK generated from that schema inherited
    the lie. Pinning the media type on the class fixes the wire *and* the spec
    from one place.
    """

    media_type = "text/event-stream"


router = APIRouter(dependencies=[Depends(require_user)])


def _apply_transparency(resp: ChatResponse, svc: TransparencyService) -> dict[str, str]:
    """Attach Article 50 disclosure + provenance to a chat response.

    No-op unless transparency is enabled. Disclosure (Art 50(1)) is written into
    ``metadata.ai_disclosure`` (body, additive — ``ChatResponse`` allows extra)
    and signalled by a header; a provenance tag (Art 50(2)) over the answer text
    is returned as the ``X-Baselith-AI-Provenance`` header. Returns the headers to
    set on the HTTP response.
    """
    if not svc.enabled:
        return {}
    headers: dict[str, str] = {}
    if svc.should_disclose():
        notice = svc.disclosure_notice()
        meta = dict(resp.metadata or {})
        meta["ai_disclosure"] = notice.to_dict()
        resp.metadata = meta
        headers[_DISCLOSURE_HEADER] = "true"
    tag = svc.mark_content(resp.answer)
    name, value = svc.provenance_header(tag)
    headers[name] = value
    return headers


def chat_run_id(request: Request) -> str:
    """The run id of this chat request (see ``derive_run_id``).

    Only an explicit ``Idempotency-Key`` from a caller the auth layer actually
    authenticated names a stable run, scoped to that caller's tenant and user
    id: a retry with the same key resumes the same durable run, or gets back
    its recorded answer. Anything else gets a fresh random id and never
    replays. The decision rests on the authenticated identity the auth
    dependency attached to the request, never on a user-id string, so an
    auth-disabled deployment (every caller anonymous) or any identity that is
    not authenticated cannot share a run. The ``X-Request-ID`` is not a replay
    key either: the client chooses it, and responses, logs and error reports
    echo it. A credential shared by several end users (one API key behind a
    service) is one principal: its holders share its keys, as with any
    idempotency scheme keyed per credential.
    """
    return stable_chat_run_id(request) or f"chat-{uuid.uuid4().hex}"


def stable_chat_run_id(request: Request) -> str | None:
    """The replayable run id :func:`chat_run_id` derives, or ``None``.

    ``None`` whenever the request names no stable run (no key, or a caller
    that is not authenticated); the stream route then skips the lookup of a
    run that cannot exist.
    """
    from baselith.core.auth.types import AuthUser
    from baselith.core.orchestration.run_ids import derive_run_id

    key = request.headers.get("idempotency-key")
    user = getattr(request.state, "user", None)
    if not key or not isinstance(user, AuthUser) or not user.is_authenticated:
        return None
    return derive_run_id(
        "chat",
        f"idempotency:{key}",
        tenant_id=user.tenant_id,
        principal=user.user_id,
    )


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses=problem_responses(401, 403, 409, 422, 429, 503),
)
async def chat(req: ChatRequest, request: Request, response: Response) -> ChatResponse:
    """
    Main endpoint for querying the agent.
    Delegated to ChatService which handles retrieval, reranking, caching, and response generation.

    An authenticated caller's ``Idempotency-Key`` names the run: a retry with
    the same key resumes the durable run, or returns its answer when it
    already finished. A retry while the first attempt still executes
    elsewhere answers **409**. Without a key, or from an anonymous caller,
    every request is a fresh run.
    """
    from baselith.core.orchestration.checkpoint_errors import (
        CheckpointLeaseHeldError,
    )

    try:
        result = await chat_service.handle_chat_async(req, run_id=chat_run_id(request))
    except CheckpointLeaseHeldError as exc:
        raise HTTPException(
            status_code=409,
            detail="This request is still being processed; retry later.",
        ) from exc
    for key, value in _apply_transparency(result, get_transparency_service()).items():
        response.headers[key] = value
    return result


async def bounded_stream(
    source: AsyncIterator[str],
    max_bytes: int,
    max_chunk_bytes: int,
) -> AsyncIterator[str]:
    """Wrap a chat stream with total and per-chunk size guards.

    Closes ``source`` on every exit — normal end, truncation, or its own
    ``aclose()`` from the SSE/WebSocket framer — so the upstream generator
    and the LLM call behind it stop instead of running on until garbage
    collection. Closing an async generator does not close the one it iterates.
    """
    try:
        total = 0
        async for chunk in source:
            if not isinstance(chunk, (str, bytes)):
                continue
            data = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            if len(data) > max_chunk_bytes:
                # Split oversized chunk to cap worst-case memory per emission.
                for i in range(0, len(data), max_chunk_bytes):
                    slice_bytes = data[i : i + max_chunk_bytes]
                    total += len(slice_bytes)
                    if total > max_bytes:
                        logger.warning(
                            "chat_stream_truncated",
                            extra={"limit_bytes": max_bytes, "total_bytes": total},
                        )
                        return
                    yield slice_bytes.decode("utf-8", errors="replace")
                continue
            total += len(data)
            if total > max_bytes:
                logger.warning(
                    "chat_stream_truncated",
                    extra={"limit_bytes": max_bytes, "total_bytes": total},
                )
                return
            yield (
                chunk
                if isinstance(chunk, str)
                else data.decode("utf-8", errors="replace")
            )
    finally:
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                logger.debug("chat_stream_source_close_failed", exc_info=True)


def sse_frame(chunk: str, event_id: int | None = None) -> str:
    """Render one model chunk as a single SSE event.

    A ``data:`` field cannot contain a raw newline, so a multi-line chunk
    becomes one ``data:`` line per line — which is exactly how a compliant
    client reassembles it (lines joined with ``\n``).

    Args:
        chunk: The text produced by the model.
        event_id: The event's ``id:`` field, when the stream numbers events.

    Returns:
        The wire-format event, terminated by the blank line SSE requires.
    """
    normalised = chunk.replace("\r\n", "\n").replace("\r", "\n")
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    data = "".join(f"data: {line}\n" for line in normalised.split("\n"))
    return id_line + data + "\n"


def sse_done_event(event_id: int | None = None) -> str:
    """Render the terminal ``event: done`` frame."""
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return id_line + SSE_DONE_EVENT


async def sse_stream(
    request: Any,
    source: AsyncIterator[str],
    timeout_seconds: float,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_SECONDS,
    *,
    skip_events: int = 0,
    reset: bool = False,
) -> AsyncIterator[str]:
    """Frame a chat stream as SSE, bounded by the client and by the clock.

    Stops early — always emitting the terminal ``event: done`` — when the
    client has disconnected, when ``timeout_seconds`` of wall clock elapse, or
    when ``source`` raises (an ``event: error`` frame goes out first), and
    closes ``source`` on the way out so the upstream generator (and the LLM
    call behind it) is released rather than left running. While the source is
    quiet, a ``: keepalive`` comment goes out every ``heartbeat_interval``
    seconds (and the client is re-checked), without cancelling the pending
    read.

    Args:
        request: Anything exposing ``await is_disconnected()`` (a Starlette
            ``Request`` in production).
        source: The chunk stream, already byte-capped by ``bounded_stream``.
        timeout_seconds: Wall-clock budget for the whole response.
        heartbeat_interval: Silence budget before a keepalive comment.
        skip_events: Chunk events the client already holds (a replayed run
            resumed with ``Last-Event-ID``): consumed and numbered, not sent.
        reset: Open with the ``event: reset`` frame (the client's partial
            text from an earlier attempt is superseded).

    Yields:
        SSE-framed events numbered with ``id:`` from 1, always ending with the
        ``event: done`` frame — preceded by ``event: error`` when the source
        failed — plus ``: keepalive`` comments during quiet periods.
    """
    deadline = time.monotonic() + timeout_seconds
    reader = HeartbeatSource(source, heartbeat_interval)
    event_id = 0
    if reset:
        yield SSE_RESET_EVENT
    try:
        while True:
            if await request.is_disconnected():
                logger.info("chat_stream_client_disconnected")
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "chat_stream_timeout",
                    extra={"timeout_seconds": timeout_seconds},
                )
                break
            try:
                chunk = await reader.next(remaining)
            except StopAsyncIteration:
                break
            if chunk is HEARTBEAT:
                if deadline - time.monotonic() > 0:
                    yield KEEPALIVE_FRAME
                continue
            event_id += 1
            if event_id > skip_events:
                yield sse_frame(chunk, event_id)
    except Exception:
        # Broad on purpose: whatever the provider, the retry wrapper or
        # bounded_stream raises, the client is already receiving a 200 and the
        # only honest way to end the exchange is in-band. CancelledError is a
        # BaseException and deliberately passes through — that is the client
        # going away, not a failure to report.
        logger.error("chat_stream_failed", exc_info=True)
        event_id += 1
        yield sse_error_event(event_id)
    finally:
        # Release the upstream generator (and the LLM call behind it) on every
        # exit path: the pending read is cancelled first, then the source is
        # closed; neither may replace the response we are writing.
        await reader.aclose()

    yield sse_done_event(event_id + 1)


@router.post(
    "/chat/stream",
    response_class=SSEResponse,
    responses=problem_responses(409),
)
async def chat_stream(req: ChatRequest, request: Request) -> SSEResponse:
    """Stream the agent response as Server-Sent Events.

    Emits one ``data:`` frame per model chunk and a final ``event: done``.
    The response is cut short — cleanly — when the client disconnects or when
    ``CHAT_STREAM_TIMEOUT_SECONDS`` elapses.

    An authenticated caller's ``Idempotency-Key`` names the run: a retry
    replays a completed answer (honouring ``Last-Event-ID``) or resumes an
    interrupted run, and answers **409** while the first attempt still
    streams elsewhere (see the module docstring).
    """
    from baselith.core.orchestration.checkpoint_errors import (
        CheckpointLeaseHeldError,
    )

    try:
        stream = await chat_service.handle_chat_stream_async(
            req, run_id=stable_chat_run_id(request)
        )
    except CheckpointLeaseHeldError as exc:
        raise HTTPException(
            status_code=409,
            detail="This request is still being processed; retry later.",
        ) from exc
    resumed_from = last_event_id(request)
    replayed = getattr(stream, "mode", None) == "replayed"
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    # Art 50(1): signal AI disclosure on the streaming surface. Provenance
    # (Art 50(2)) needs the full output hash and is omitted here to avoid
    # buffering the stream — it is emitted on the non-streaming /chat endpoint.
    svc = get_transparency_service()
    if svc.enabled and svc.should_disclose():
        headers[_DISCLOSURE_HEADER] = "true"
    timeout_seconds = float(
        getattr(
            get_app_config(),
            "chat_stream_timeout_seconds",
            DEFAULT_STREAM_TIMEOUT_SECONDS,
        )
    )
    return SSEResponse(
        sse_stream(
            request,
            bounded_stream(stream, STREAM_MAX_BYTES, STREAM_MAX_CHUNK_BYTES),
            timeout_seconds,
            heartbeat_seconds(),
            skip_events=resumed_from if replayed else 0,
            reset=resumed_from > 0 and not replayed,
        ),
        headers=headers,
    )
