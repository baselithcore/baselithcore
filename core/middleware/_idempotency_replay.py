"""Request-side helpers for idempotency: who may replay, and what is replayed.

Helpers behind :class:`core.middleware.idempotency.IdempotencyMiddleware`, kept
apart for the module size cap:

- :func:`credential_verified` gates replay and storage on a credential that
  actually authenticates.

- :class:`BodyFingerprint` wraps ``receive`` and hashes the request body *as
  the application reads it* — one streaming SHA-256, never a second copy of
  the body in memory. The digest is stored beside the captured response.
- :func:`drain_body_digest` hashes a retry's body on the replay path, where
  the application never runs, so a retry that reuses a key with a different
  payload is refused (``422``, per ``draft-ietf-httpapi-idempotency-key-header``)
  instead of being answered with the first payload's response.
- :func:`decode_entry` / :func:`negotiate_encoding` turn a stored entry back
  into a response the retry can accept: compression runs *inside* the
  idempotency layer, so a stored body may be gzip-encoded while the retry does
  not accept gzip — it is decompressed rather than replayed as-is.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
from dataclasses import dataclass
from typing import Any

import orjson
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from core.auth import AuthUser
from core.middleware._auth_memo import resolve_user


async def credential_verified(scope: Scope) -> bool:
    """Whether the credential on this request actually authenticates.

    Replay and storage are keyed on the raw credential header, so without this
    any made-up ``Authorization``/``X-API-Key`` value — even an unsupported
    scheme — earned its own bucket, and an unauthenticated client could fill
    Redis one junk header at a time. Uses the per-request memo of
    :mod:`core.middleware._auth_memo`, which the tenant layer (outside the
    idempotency layer) has already filled, so this costs no verification.
    """
    state = scope.get("state")
    preset = (
        state.get("user") if isinstance(state, dict) else getattr(state, "user", None)
    ) or scope.get("user")
    if isinstance(preset, AuthUser):
        return preset.is_authenticated
    user = await resolve_user(scope)
    return user is not None and user.is_authenticated


class BodyFingerprint:
    """A ``receive`` wrapper that hashes the request body while forwarding it.

    ``digest`` is only meaningful once ``complete`` — the final
    ``http.request`` frame (``more_body`` false) went through. A handler that
    never reads its body leaves it incomplete; the middleware then stores no
    fingerprint (the handler's result cannot depend on a body it never read).
    """

    def __init__(self, receive: Receive) -> None:
        self._receive = receive
        self._hash = hashlib.sha256()
        self.complete = False

    async def __call__(self) -> Message:
        message = await self._receive()
        if message["type"] == "http.request" and not self.complete:
            self._hash.update(message.get("body", b"") or b"")
            if not message.get("more_body", False):
                self.complete = True
        return message

    @property
    def digest(self) -> str | None:
        """Hex SHA-256 of the whole body, or ``None`` if not fully read."""
        return self._hash.hexdigest() if self.complete else None


async def drain_body_digest(receive: Receive) -> str | None:
    """Read the rest of the request body, hashing it; ``None`` on disconnect."""
    fingerprint = BodyFingerprint(receive)
    while not fingerprint.complete:
        message = await fingerprint()
        if message["type"] == "http.disconnect":
            return None
    return fingerprint.digest


@dataclass(frozen=True, slots=True)
class StoredResponse:
    """A decoded idempotency cache entry."""

    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes
    #: SHA-256 of the request body that produced it; ``None`` for entries
    #: written before fingerprinting existed or whose body was never read.
    body_sha256: str | None


def encode_entry(
    status: int,
    headers: list[Any],
    body: bytes,
    body_sha256: str | None,
) -> bytes:
    """Serialise a captured response (and its request fingerprint) for Redis."""
    encoded: bytes = orjson.dumps(
        {
            "status": status,
            "headers": [[k.decode("latin-1"), v.decode("latin-1")] for k, v in headers],
            "body": base64.b64encode(body).decode("ascii"),
            "body_sha256": body_sha256,
        }
    )
    return encoded


def decode_entry(stored: Any) -> StoredResponse | None:
    """Parse a stored entry (bytes or str); ``None`` when it is corrupt."""
    try:
        payload = orjson.loads(stored)
        digest = payload.get("body_sha256")
        return StoredResponse(
            status=int(payload["status"]),
            headers=[
                (str(k).encode("latin-1"), str(v).encode("latin-1"))
                for k, v in payload["headers"]
            ],
            body=base64.b64decode(payload["body"]),
            body_sha256=str(digest) if digest else None,
        )
    # Marker on the ``except`` line itself — the hygiene gate reads only that.
    except Exception:  # silent-ok: corrupt entry = no replay; the request runs
        return None


def negotiate_encoding(
    entry: StoredResponse, accept_encoding: str
) -> tuple[list[tuple[bytes, bytes]], bytes]:
    """Headers and body to replay for a retry sending ``accept_encoding``.

    A gzip-encoded entry goes out untouched to a client that accepts gzip (the
    same substring test ``SmartGzipMiddleware`` applies) and decompressed —
    ``Content-Encoding`` dropped, ``Content-Length`` recomputed — to one that
    does not. An undecompressible body is replayed as stored.
    """
    headers = list(entry.headers)
    encoding = next(
        (v.lower() for k, v in headers if k.lower() == b"content-encoding"), b""
    )
    if encoding != b"gzip" or "gzip" in accept_encoding.lower():
        return headers, entry.body
    try:
        body = gzip.decompress(entry.body)
    except (OSError, EOFError):
        return headers, entry.body
    headers = [
        (k, v)
        for k, v in headers
        if k.lower() not in (b"content-encoding", b"content-length")
    ]
    headers.append((b"content-length", str(len(body)).encode("latin-1")))
    return headers, body


async def replay_entry(
    entry: StoredResponse,
    accept_encoding: str,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    """Replay ``entry`` to a retry — or ``422`` it if its body differs.

    Entries without a fingerprint (older entries, or a handler that never read
    its body) replay unconditionally, as before.
    """
    if entry.body_sha256 is not None:
        digest = await drain_body_digest(receive)
        if digest is None:
            return  # client went away mid-body; nobody to answer
        if digest != entry.body_sha256:
            await JSONResponse(
                status_code=422,
                content={
                    "detail": "Idempotency-Key was already used with a "
                    "different request body."
                },
            )(scope, receive, send)
            return
    headers, body = negotiate_encoding(entry, accept_encoding)
    headers.append((b"idempotency-replayed", b"true"))
    await send(
        {"type": "http.response.start", "status": entry.status, "headers": headers}
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


__all__ = [
    "BodyFingerprint",
    "StoredResponse",
    "credential_verified",
    "decode_entry",
    "drain_body_digest",
    "encode_entry",
    "negotiate_encoding",
    "replay_entry",
]
