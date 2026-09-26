"""Bounded request-body reader for the unauthenticated inbound webhook.

``await request.body()`` buffers the whole payload before the caller can
check its size, so a 413 decided afterwards protects nothing: an anonymous
client could stream gigabytes into process memory ahead of any signature
check. This reader refuses on a declared ``Content-Length`` first, then
counts while streaming so a chunked body (no length header) or a lying
header is cut off at the cap.
"""

from __future__ import annotations

from fastapi import HTTPException, Request


def _too_large(limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail=f"body exceeds {limit} bytes")


async def read_body_capped(request: Request, limit: int) -> bytes:
    """Return the request body, raising 413 as soon as it exceeds ``limit``.

    Args:
        request: The incoming request.
        limit: Maximum accepted body size in bytes.

    Raises:
        HTTPException: 413 when the declared or streamed size exceeds
            ``limit``; 400 on a malformed ``Content-Length``.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise _too_large(limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise _too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks)


__all__ = ["read_body_capped"]
