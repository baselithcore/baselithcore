"""Shared HTTP client with retry/backoff for the TEI-backed services."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from core.observability.logging import get_logger
from core.services.inference.errors import InferenceError

logger = get_logger(__name__)

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class RemoteClient:
    """One shared ``httpx.AsyncClient`` plus a retrying ``post_json``.

    Retries on timeouts, connection errors and 5xx/429 with exponential
    backoff; any other 4xx fails immediately (retrying a malformed request
    cannot succeed).
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        max_retries: int,
        backoff_base: float,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            transport=transport,
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        """POST ``payload`` and return the decoded JSON body."""
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(path, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            else:
                if resp.status_code < 400:
                    return resp.json()
                if resp.status_code not in _RETRYABLE_STATUS:
                    raise InferenceError(
                        f"POST {path} failed: HTTP {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
                last = InferenceError(f"POST {path}: HTTP {resp.status_code}")
            if attempt < self._max_retries:
                delay = self._backoff_base * (2**attempt)
                logger.warning(
                    "inference_retry", path=path, attempt=attempt + 1, delay=delay
                )
                await asyncio.sleep(delay)
        raise InferenceError(
            f"POST {path} failed after {self._max_retries + 1} attempts: {last}"
        ) from last

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()
