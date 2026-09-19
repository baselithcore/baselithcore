"""Redis primitives behind :class:`core.middleware.idempotency.IdempotencyMiddleware`.

Extracted to keep the middleware module under the 500-line cap. Holds the
atomic replay-or-lock step and the unverified ``exp`` reader the replay TTL
is capped with; the middleware itself owns key derivation, capture and store.
"""

from __future__ import annotations

import base64
from typing import Any

import orjson

# Atomic replay-or-lock step, ONE round trip: hand back the stored response if
# there is one, otherwise claim the in-flight lock (SET NX EX). This used to be
# two sequential calls — GET, then SET NX — i.e. two Redis latencies on the hot
# path of every keyed mutating request, plus a third GET whenever the lock was
# already held: the re-check that covered the gap between the GET and the SET,
# in which a concurrent duplicate could finish and store. Inside one script
# there is no gap — a stored response is seen first, and a missing one with
# the lock held means the duplicate is still running — so the 409 needs no
# re-check either. Single-node keys (no Cluster hash tag): the storage and lock
# keys of one request always live on the one Redis the cache client points at.
#   KEYS[1] storage key   KEYS[2] lock key   ARGV[1] lock TTL (seconds)
#   returns {1, payload} = replay | {2} = lock acquired | {3} = still in flight
REPLAY_OR_LOCK_LUA = """
local stored = redis.call('GET', KEYS[1])
if stored then
  return {1, stored}
end
if redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[1]) then
  return {2}
end
return {3}
"""


async def replay_or_lock(
    redis: Any,
    script: Any,
    storage_key: str,
    lock_key: str,
    lock_ttl: int,
) -> tuple[Any, bool]:
    """Return ``(stored_payload, lock_acquired)`` for one keyed request.

    ``script`` is the registered :data:`REPLAY_OR_LOCK_LUA` (one round trip).
    ``None`` — a client without ``register_script``, such as a minimal test
    double — falls back to the sequential GET + SET NX pair, which then also
    has to look once more after a failed SET (the duplicate may have stored
    in between) before the caller answers 409. Storage errors propagate; the
    middleware treats them as fail-open.
    """
    if script is not None:
        result = await script(keys=[storage_key, lock_key], args=[lock_ttl])
        code = int(result[0])
        if code == 1:
            return result[1], False
        return None, code == 2
    stored = await redis.get(storage_key)
    if stored:
        return stored, False
    acquired = await redis.set(lock_key, "1", nx=True, ex=lock_ttl)
    if not acquired:
        stored = await redis.get(storage_key)
        if stored:
            return stored, False
    return None, bool(acquired)


def jwt_exp(token: str) -> float | None:
    """Read the ``exp`` claim of a compact JWS without verifying it.

    Only ever used to *shorten* a replay TTL, so an unverifiable or forged
    value cannot widen anything: the route's own verification already decided
    whether the credential was good when the response was stored. ``None``
    for anything that is not a three-part token with a numeric ``exp``.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = orjson.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        return float(exp) if isinstance(exp, (int, float)) else None
    # The marker must sit on the ``except`` line itself — the hygiene gate reads
    # that one line — so the reason is kept short enough to stay unwrapped.
    except Exception:  # silent-ok: unreadable token = no cap, never a longer one
        return None


__all__ = ["REPLAY_OR_LOCK_LUA", "jwt_exp", "replay_or_lock"]
