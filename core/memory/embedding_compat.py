"""Uniform async/sync embedder invocation for the memory layer.

Several call sites awaited ``embedder.encode(...)`` directly: with an async
embedder that works, but a plain sync embedder either blocks the event loop
or raises ``TypeError`` inside a broad ``except`` — silently degrading recall
to keyword search. This helper applies the one correct pattern everywhere
(``iscoroutinefunction`` check, sync encode offloaded to a worker thread,
numpy output normalized to a list).
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any


async def encode_flexible(embedder: Any, payload: Any) -> Any:
    """Encode ``payload`` with an async OR sync embedder, off-loop when sync.

    Returns the embedder's output with numpy arrays converted via ``tolist``.
    Exceptions propagate — callers own their fallback semantics.
    """
    if inspect.iscoroutinefunction(embedder.encode):
        encoded = await embedder.encode(payload)
    else:
        encoded = await asyncio.to_thread(embedder.encode, payload)
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    return encoded


__all__ = ["encode_flexible"]
