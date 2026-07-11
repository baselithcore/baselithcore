"""Idempotency-Key middleware (pure ASGI).

Lets clients safely retry mutating requests (``POST``/``PUT``/``PATCH``/
``DELETE``) without duplicating side effects: a request carrying an
``Idempotency-Key`` header has its response captured and stored; a later request
with the same key replays that stored response instead of re-executing.

Design notes:

- **Pure ASGI** (no ``BaseHTTPMiddleware``) so it never wraps the request in an
  extra anyio task and stays streaming-safe.
- **Streaming pass-through**: a response whose ``Content-Type`` is
  ``text/event-stream`` (or which exceeds the body cap) is forwarded chunk by
  chunk and never buffered/cached — SSE endpoints are unaffected.
- **Fail-open**: if Redis is unavailable or anything goes wrong on the storage
  path, the request proceeds normally (idempotency is best-effort, never a
  hard dependency that can take the API down).
- ``5xx`` responses are not cached (they are usually transient — a retry should
  get a fresh attempt).

Follows the IETF ``Idempotency-Key`` header draft / the Stripe model.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

import orjson
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config
from core.context import get_current_tenant_id
from core.observability.logging import get_logger

logger = get_logger(__name__)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MAX_KEY_LENGTH = 255


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


class IdempotencyMiddleware:
    """Replay stored responses for repeated ``Idempotency-Key`` requests."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        ttl_seconds: int | None = None,
        max_body_bytes: int | None = None,
    ) -> None:
        self.app = app
        self.enabled = _flag("BASELITH_IDEMPOTENCY_ENABLED", True)
        self.ttl_seconds = ttl_seconds or int(
            os.getenv("BASELITH_IDEMPOTENCY_TTL_SECONDS", "86400")
        )
        self.max_body_bytes = max_body_bytes or int(
            os.getenv("BASELITH_IDEMPOTENCY_MAX_BODY_BYTES", str(1024 * 1024))
        )
        cache_config = get_redis_cache_config()
        self._prefix = cache_config.cache_prefix + ":idem:"
        self._redis: Any = None
        if self.enabled:
            try:
                self._redis = create_redis_client(cache_config.url)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "Idempotency Redis unavailable (%s); middleware disabled",
                    type(e).__name__,
                )
                self._redis = None

    def _header(self, scope: Scope, name: bytes) -> str | None:
        for key, value in scope.get("headers", []):
            if key == name:
                return value.decode("latin-1")
        return None

    def _storage_key(self, scope: Scope, idem_key: str) -> str:
        # Best-effort tenant scoping. At the middleware layer the authenticated
        # tenant is usually not resolved yet (auth runs in the route
        # dependency), so this is defense-in-depth on top of the opaque,
        # client-unique key — never a correctness dependency. Fail safe under
        # strict tenant isolation rather than 500 the request.
        try:
            tenant = get_current_tenant_id() or "default"
        except Exception:
            tenant = "default"
        key_hash = hashlib.sha256(idem_key.encode("utf-8")).hexdigest()
        return f"{self._prefix}{tenant}:{scope['method']}:{scope['path']}:{key_hash}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            not self.enabled
            or self._redis is None
            or scope["type"] != "http"
            or scope["method"] not in _MUTATING_METHODS
        ):
            await self.app(scope, receive, send)
            return

        idem_key = self._header(scope, b"idempotency-key")
        if not idem_key:
            await self.app(scope, receive, send)
            return
        if len(idem_key) > _MAX_KEY_LENGTH:
            await JSONResponse(
                status_code=400,
                content={"detail": "Idempotency-Key exceeds maximum length."},
            )(scope, receive, send)
            return

        storage_key = self._storage_key(scope, idem_key)
        lock_key = storage_key + ":lock"

        # 1) Replay a stored result if present.
        replayed = await self._try_replay(storage_key, send)
        if replayed:
            return

        # 2) Claim the in-flight lock. If someone else holds it, re-check the
        #    result (they may have just finished) then fail with 409.
        try:
            acquired = await self._redis.set(
                lock_key, "1", nx=True, ex=max(1, min(self.ttl_seconds, 300))
            )
        except Exception:
            await self.app(scope, receive, send)  # fail-open
            return

        if not acquired:
            if await self._try_replay(storage_key, send):
                return
            await JSONResponse(
                status_code=409,
                content={
                    "detail": "A request with this Idempotency-Key is already "
                    "in progress."
                },
            )(scope, receive, send)
            return

        # 3) Run the app, capturing the response unless it streams / is too big.
        await self._run_and_capture(scope, receive, send, storage_key, lock_key)

    async def _try_replay(self, storage_key: str, send: Send) -> bool:
        try:
            stored = await self._redis.get(storage_key)
        except Exception:
            return False
        if not stored:
            return False
        try:
            payload = orjson.loads(stored)
            body = base64.b64decode(payload["body"])
            headers = [
                (k.encode("latin-1"), v.encode("latin-1"))
                for k, v in payload["headers"]
            ]
        except Exception:  # pragma: no cover - corrupt cache entry
            return False
        headers.append((b"idempotency-replayed", b"true"))
        await send(
            {
                "type": "http.response.start",
                "status": int(payload["status"]),
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
        return True

    async def _run_and_capture(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        storage_key: str,
        lock_key: str,
    ) -> None:
        state: dict[str, Any] = {
            "status": 200,
            "headers": [],
            "chunks": [],
            "size": 0,
            "cacheable": True,
            "started": False,
        }

        async def capture(message: Message) -> None:
            msg_type = message["type"]
            if msg_type == "http.response.start":
                state["status"] = message["status"]
                state["headers"] = message.get("headers", [])
                content_type = b""
                for k, v in state["headers"]:
                    if k.lower() == b"content-type":
                        content_type = v.lower()
                        break
                # Don't buffer streams or server errors.
                if content_type.startswith(b"text/event-stream") or (
                    message["status"] >= 500
                ):
                    state["cacheable"] = False
                if not state["cacheable"]:
                    state["started"] = True
                    await send(message)
                return

            if msg_type != "http.response.body":
                await send(message)
                return

            body = message.get("body", b"") or b""
            more = message.get("more_body", False)

            if not state["cacheable"]:
                await send(message)
                return

            state["chunks"].append(body)
            state["size"] += len(body)
            if state["size"] > self.max_body_bytes:
                # Too large to cache: flush what we withheld, then pass through.
                state["cacheable"] = False
                if not state["started"]:
                    state["started"] = True
                    await send(
                        {
                            "type": "http.response.start",
                            "status": state["status"],
                            "headers": state["headers"],
                        }
                    )
                for i, chunk in enumerate(state["chunks"]):
                    await send(
                        {
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": not (
                                i == len(state["chunks"]) - 1 and not more
                            ),
                        }
                    )
                state["chunks"] = []
                return

            if not more:
                # Complete + cacheable: persist, release lock, then emit.
                full_body = b"".join(state["chunks"])
                await self._store(
                    storage_key, lock_key, state["status"], state["headers"], full_body
                )
                await send(
                    {
                        "type": "http.response.start",
                        "status": state["status"],
                        "headers": state["headers"],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": full_body,
                        "more_body": False,
                    }
                )

        try:
            await self.app(scope, receive, capture)
        except Exception:
            await self._release(lock_key)
            raise
        # If the response streamed (never cached), drop the lock so a genuine
        # retry isn't blocked for the full TTL.
        if not state["cacheable"]:
            await self._release(lock_key)

    async def _store(
        self,
        storage_key: str,
        lock_key: str,
        status: int,
        headers: list[Any],
        body: bytes,
    ) -> None:
        try:
            # orjson emits bytes — Redis accepts them directly, and decoding
            # replayed entries accepts bytes and str alike, so entries written
            # by the previous stdlib-json code still parse.
            payload = orjson.dumps(
                {
                    "status": status,
                    "headers": [
                        [k.decode("latin-1"), v.decode("latin-1")] for k, v in headers
                    ],
                    "body": base64.b64encode(body).decode("ascii"),
                }
            )
            await self._redis.set(storage_key, payload, ex=self.ttl_seconds)
            await self._release(lock_key)
        except Exception:  # pragma: no cover - storage best-effort
            logger.warning("Idempotency store failed for %s", storage_key)

    async def _release(self, lock_key: str) -> None:
        try:
            await self._redis.delete(lock_key)
        except Exception:  # pragma: no cover
            pass


__all__ = ["IdempotencyMiddleware"]
