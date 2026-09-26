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
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from core.chat import chat_service
from core.config import get_app_config
from core.middleware import require_user
from core.models.chat import ChatRequest, ChatResponse
from core.observability.logging import get_logger
from core.transparency import TransparencyService, get_transparency_service

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
# dropped socket. The payload is deliberately fixed: the exception text can carry
# provider detail, prompt fragments or credentials, and it is already in the log.
SSE_ERROR_EVENT = "event: error\ndata: stream failed\n\n"

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


@router.post("/chat")
async def chat(req: ChatRequest, response: Response):
    """
    Main endpoint for querying the agent.
    Delegated to ChatService which handles retrieval, reranking, caching, and response generation.
    """
    result = await chat_service.handle_chat_async(req)
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


def sse_frame(chunk: str) -> str:
    """Render one model chunk as a single SSE event.

    A ``data:`` field cannot contain a raw newline, so a multi-line chunk
    becomes one ``data:`` line per line — which is exactly how a compliant
    client reassembles it (lines joined with ``\n``).

    Args:
        chunk: The text produced by the model.

    Returns:
        The wire-format event, terminated by the blank line SSE requires.
    """
    normalised = chunk.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(f"data: {line}\n" for line in normalised.split("\n")) + "\n"


async def sse_stream(
    request: Any,
    source: AsyncIterator[str],
    timeout_seconds: float,
) -> AsyncIterator[str]:
    """Frame a chat stream as SSE, bounded by the client and by the clock.

    Stops early — always emitting the terminal ``event: done`` — when the
    client has disconnected, when ``timeout_seconds`` of wall clock elapse, or
    when ``source`` raises (an ``event: error`` frame goes out first), and
    closes ``source`` on the way out so the upstream generator (and the LLM
    call behind it) is released rather than left running.

    Args:
        request: Anything exposing ``await is_disconnected()`` (a Starlette
            ``Request`` in production).
        source: The chunk stream, already byte-capped by ``bounded_stream``.
        timeout_seconds: Wall-clock budget for the whole response.

    Yields:
        SSE-framed events, always ending with ``SSE_DONE_EVENT`` — preceded by
        ``SSE_ERROR_EVENT`` when the source failed.
    """
    deadline = time.monotonic() + timeout_seconds
    iterator = source.__aiter__()
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
                chunk = await asyncio.wait_for(iterator.__anext__(), remaining)
            except StopAsyncIteration:
                break
            except TimeoutError:
                logger.warning(
                    "chat_stream_timeout",
                    extra={"timeout_seconds": timeout_seconds},
                )
                break
            yield sse_frame(chunk)
    except Exception:
        # Broad on purpose: whatever the provider, the retry wrapper or
        # bounded_stream raises, the client is already receiving a 200 and the
        # only honest way to end the exchange is in-band. CancelledError is a
        # BaseException and deliberately passes through — that is the client
        # going away, not a failure to report.
        logger.error("chat_stream_failed", exc_info=True)
        yield SSE_ERROR_EVENT
    finally:
        # Release the upstream generator (and the LLM call behind it) on every
        # exit path. A source already finished, cancelled by the timeout above,
        # or mid-``athrow`` can raise from ``aclose``; that must not replace the
        # response we are in the middle of writing, so it is logged, not raised.
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                logger.debug("chat_stream_source_close_failed", exc_info=True)

    yield SSE_DONE_EVENT


@router.post("/chat/stream", response_class=SSEResponse)
async def chat_stream(req: ChatRequest, request: Request) -> SSEResponse:
    """Stream the agent response as Server-Sent Events.

    Emits one ``data:`` frame per model chunk and a final ``event: done``.
    The response is cut short — cleanly — when the client disconnects or when
    ``CHAT_STREAM_TIMEOUT_SECONDS`` elapses.
    """
    stream = await chat_service.handle_chat_stream_async(req)
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
        ),
        headers=headers,
    )
