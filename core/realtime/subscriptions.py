"""Consuming a Redis subscription without losing it — or its pooled connection.

Two properties of the shared pool
(:func:`core.cache.redis_cache.create_redis_client`) make the obvious
subscriber loop wrong, and both fail silently:

* **The pool sets a read deadline.** ``socket_timeout`` exists so a server that
  accepts a connection and then stops answering cannot hang a caller forever.
  redis-py applies it to every read that carries no deadline of its own —
  ``read_timeout = timeout if timeout is not None else self.socket_timeout``
  (``redis/asyncio/connection.py``) — and the blocking read behind
  ``PubSub.listen()`` carries none. So ``async for message in pubsub.listen()``
  raises ``TimeoutError: Timeout reading from <host>`` after every
  ``socket_timeout`` seconds of silence and redis-py disconnects the socket.
  A reconnect loop then looks healthy while it re-subscribes forever, dropping
  every event published in the gap. ``get_message(timeout=…)`` passes its own
  deadline down instead, so silence returns ``None`` and the subscription
  survives — that is what :func:`iter_messages` polls with.

* **The pool is bounded and accounts by object.** A ``PubSub`` checks one
  connection out of the 50 for the whole subscription, and only
  ``PubSub.aclose()`` calls ``connection_pool.release()``. ``unsubscribe()``
  only sends the command; ``Redis.aclose()`` releases the *client's* own
  connection, which a pubsub user never has. An abandoned subscription stays in
  the pool's ``_in_use_connections`` set forever — even after its socket dies —
  until every Redis caller in the worker raises
  ``ConnectionError("Too many connections")``, blaming whoever asked next.
  :func:`close_pubsub` is the one cleanup that gives the connection back.

Both were found together: a lab worker exhausted its pool ~16 minutes after
every boot, one leaked connection per reconnect cycle, while Redis itself held
seventeen healthy clients and reported nothing wrong.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

#: Poll deadline for an idle subscription. Any positive value keeps the
#: subscription alive (the deadline is *ours*, so its expiry returns ``None``
#: instead of dropping the socket); the value only decides how often the loop
#: wakes to notice cancellation and to let redis-py run its health-check PING.
DEFAULT_IDLE_TIMEOUT = 5.0

__all__ = ["DEFAULT_IDLE_TIMEOUT", "close_pubsub", "iter_messages"]


async def iter_messages(
    pubsub: Any,
    *,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    ignore_subscribe_messages: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """Yield messages from ``pubsub`` forever, riding out idle periods.

    Replaces ``async for message in pubsub.listen()``, which cannot outlive the
    pool's ``socket_timeout`` (see the module docstring). Silence is not an
    event: idle polls are swallowed, so the caller sees only real messages.

    Args:
        pubsub: A subscribed ``redis.asyncio.client.PubSub``.
        idle_timeout: Seconds to wait per poll before looping again.
        ignore_subscribe_messages: Drop the ``subscribe``/``unsubscribe``
            confirmations, as ``PubSub.get_message`` does.

    Yields:
        Each message as redis-py returns it (``type``, ``channel``, ``data``).
    """
    while True:
        message = await pubsub.get_message(
            ignore_subscribe_messages=ignore_subscribe_messages,
            timeout=idle_timeout,
        )
        if message is None:  # nothing published within the deadline
            continue
        yield message


async def close_pubsub(pubsub: Any) -> None:
    """Return a subscription's connection to the shared pool (never raises).

    Safe with ``None`` and safe to call twice, so it fits straight into a
    ``finally`` on paths that may not have subscribed at all.

    Args:
        pubsub: The subscription to close, or ``None``.
    """
    if pubsub is None:
        return
    try:
        await pubsub.aclose()
    except Exception:  # silent-ok: cleanup must never mask the caller's own error
        pass
