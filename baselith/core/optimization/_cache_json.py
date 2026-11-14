"""JSON text for :class:`~baselith.core.optimization.caching.RedisCache` values."""

from __future__ import annotations

import json
from typing import Any

import orjson


def _dumps(value: Any) -> str:
    """Serialise a cache value to JSON text.

    orjson first: this class is the vector store's embedding cache, and the
    stdlib encoder spent ~0.2 ms per 1024-dimension vector on the event loop
    (ten times orjson). The text stays plain JSON, so entries written by
    either encoder are read by either decoder. The stdlib remains the
    fallback for what orjson refuses (non-string keys, integers past 64 bits).
    """
    try:
        text: str = orjson.dumps(value).decode()
        return text
    except TypeError:
        return json.dumps(value)


def _loads(text: str) -> Any:
    """Parse cached JSON text; raises ``json.JSONDecodeError`` on non-JSON."""
    try:
        return orjson.loads(text)
    except orjson.JSONDecodeError:
        # ``NaN``/``Infinity``, which the stdlib writes and orjson rejects.
        return json.loads(text)


__all__ = ["_dumps", "_loads"]
