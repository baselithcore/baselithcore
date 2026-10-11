"""The streaming chat request: from a ``ChatRequest`` to a chunk stream.

Split from :mod:`baselith.core.services.chat.service` (module size cap). With
a ``run_id`` the stream is opened as a named, durable run through
:func:`~baselith.core.orchestration.stream_runs.open_stream_run`, and the
returned :class:`ChatStream` says which kind of attempt it is, so the SSE
layer can honour ``Last-Event-ID`` (see ``baselith.plugins.api_routers.chat``).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint_errors import CheckpointLeaseHeldError
from baselith.core.services.chat.utils.conversation import (
    HISTORY_TEXT_KEY,
    load_history,
    recording_stream,
)
from baselith.core.services.chat.utils.streaming import on_stream_end

if TYPE_CHECKING:
    from baselith.core.models.chat import ChatRequest
    from baselith.core.services.chat.service import ChatService

logger = get_logger(__name__)

__all__ = ["ChatStream", "open_chat_stream"]


class ChatStream:
    """A chat chunk stream that also names the run it belongs to.

    An async iterator like any other (``aclose`` closes the source); the extra
    attributes tell a framer what the chunks are.

    Attributes:
        run_id: The run, or ``None`` for an unnamed one.
        mode: ``fresh``, ``resumed``, ``replayed`` or ``paused`` (see
            :mod:`baselith.core.orchestration.stream_runs`). Only a
            ``replayed`` stream re-emits events a client may already hold.
    """

    def __init__(
        self, source: AsyncIterator[str], *, run_id: str | None, mode: str
    ) -> None:
        self._source = source
        self.run_id = run_id
        self.mode = mode

    def __aiter__(self) -> ChatStream:
        return self

    async def __anext__(self) -> str:
        return await self._source.__anext__()

    async def aclose(self) -> None:
        """Close the underlying stream (and the run behind it)."""
        aclose = getattr(self._source, "aclose", None)
        if aclose is not None:
            await aclose()


async def open_chat_stream(
    service: ChatService, req: ChatRequest, run_id: str | None
) -> AsyncIterator[str]:
    """Body of :meth:`ChatService.handle_chat_stream_async` (see there).

    Raises:
        CheckpointLeaseHeldError: ``run_id`` names a run another executor is
            still streaming; raised before any chunk exists.
    """
    start = time.perf_counter()
    try:
        service._record_metric("chat_requests_total", route="stream")

        if not service.config.streaming_enabled:
            response = await service.handle_chat_async(req, run_id=run_id)

            async def _single_response() -> AsyncIterator[str]:
                yield response.answer

            return _single_response()

        history_key = service._history_key(req)
        turns, history_text = await load_history(service.history_manager, history_key)
        context: dict[str, Any] = {
            "conversation_id": req.conversation_id,
            "rag_only": req.rag_only,
            "kb_label": req.kb_label,
            HISTORY_TEXT_KEY: history_text,
        }

        def _latency() -> None:
            # Latency on success is the whole generation, observed when the
            # consumer finishes the stream — not when it was merely built.
            service._record_metric(
                "chat_request_latency",
                route="stream",
                value=time.perf_counter() - start,
            )

        if run_id is None:
            return on_stream_end(
                recording_stream(
                    service.agent.process_stream(req.query, context),
                    service.history_manager,
                    history_key,
                    turns,
                    req.query,
                ),
                _latency,
            )

        from baselith.core.orchestration.stream_runs import (
            MODE_PAUSED,
            open_stream_run,
        )

        opened = await open_stream_run(service.agent, req.query, context, run_id)
        source: AsyncIterator[str] = opened.source
        if opened.mode != MODE_PAUSED:
            # A paused run's notice is not an answer worth remembering.
            source = recording_stream(
                source, service.history_manager, history_key, turns, req.query
            )
        return ChatStream(
            on_stream_end(source, _latency), run_id=opened.run_id, mode=opened.mode
        )

    except CheckpointLeaseHeldError:
        service._record_metric("chat_request_errors_total", route="stream")
        raise
    except Exception:
        service._record_metric("chat_request_errors_total", route="stream")
        service._record_metric(
            "chat_request_latency",
            route="stream",
            value=time.perf_counter() - start,
        )
        logger.exception("Async chat streaming pipeline failed")

        async def _error_stream() -> AsyncIterator[str]:
            yield "❌ Critical internal error during generation."

        return _error_stream()
