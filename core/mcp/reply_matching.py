"""Which JSON-RPC message is the reply to *our* request.

A server shares the wire with us: it may interleave notifications and, worse,
send its own requests — and nothing stops it reusing the id of a request we
have in flight. Matching on ``id`` alone therefore lets a hostile server pass
off a ``sampling/createMessage`` request as the result of our call. A message
is the reply only when it carries ``result`` or ``error``, carries no
``method``, and its id is *exactly* ours (same JSON type: ``True`` is not
``1`` and ``"1"`` is not ``1``).
"""

from __future__ import annotations

from typing import Any


def _same_id(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def is_reply(message: dict[str, Any], request_id: Any) -> bool:
    """Whether *message* is the response to the request with *request_id*."""
    if "method" in message or "id" not in message:
        return False
    if "result" not in message and "error" not in message:
        return False
    return _same_id(message["id"], request_id)


def is_server_request(message: dict[str, Any]) -> bool:
    """A request the server is making of us: ``method`` plus a non-null ``id``."""
    return "method" in message and message.get("id") is not None


def is_notification(message: dict[str, Any]) -> bool:
    """A one-way server message: ``method`` and no ``id``."""
    return "method" in message and "id" not in message


__all__ = ["is_notification", "is_reply", "is_server_request"]
