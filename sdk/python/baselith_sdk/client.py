"""Synchronous and asynchronous clients for the BaselithCore API.

Both clients share the same surface — ``chat``, ``chat_stream``,
``submit_feedback``, ``health``, ``readiness`` — and the same construction:

    from baselith_sdk import BaselithClient

    client = BaselithClient("https://api.example.com", api_key="sk-...")
    print(client.chat("hello").answer)

Features:

* Auth via API key (``x-api-key``) or bearer token (``Authorization``).
* Automatic retry with exponential backoff + jitter on 429/5xx, honouring
  ``Retry-After``.
* Idempotency keys on mutating requests (auto-generated unless supplied).
* Versioned routing (``/v1`` by default) with liveness probes left unprefixed.
* Typed responses and a typed error hierarchy parsed from the API's error
  envelope.
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Any, AsyncIterator, Iterator

import httpx

from .errors import (
    APIConnectionError,
    BaselithConfigError,
    error_from_response,
)
from .models import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    HealthStatus,
    ReadinessStatus,
)
from .version import __version__

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 2
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_USER_AGENT = f"baselith-sdk-python/{__version__}"


def _build_headers(
    api_key: str | None,
    bearer_token: str | None,
    tenant_id: str | None,
) -> dict[str, str]:
    """Assemble the static default headers for every request."""
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    return headers


def _backoff_seconds(attempt: int, retry_after: float | None) -> float:
    """Exponential backoff with jitter; respect a server Retry-After hint."""
    if retry_after is not None and retry_after >= 0:
        return retry_after
    return min(2.0**attempt, 30.0) + random.uniform(0, 0.5)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _decode_body(response: httpx.Response) -> Any:
    """Best-effort JSON decode, falling back to text."""
    ctype = response.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            return response.json()
        except Exception:
            return response.text
    return response.text


class _ClientBase:
    """Shared configuration and URL/header construction for both clients."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        bearer_token: str | None = None,
        tenant_id: str | None = None,
        api_version: str | None = "v1",
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        if not base_url:
            raise BaselithConfigError("base_url is required")
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version.strip("/") if api_version else None
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        self._default_headers = _build_headers(api_key, bearer_token, tenant_id)

    def _url(self, path: str, *, versioned: bool = True) -> str:
        path = "/" + path.lstrip("/")
        if versioned and self._api_version:
            return f"{self._base_url}/{self._api_version}{path}"
        return f"{self._base_url}{path}"

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = dict(self._default_headers)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers


class BaselithClient(_ClientBase):
    """Synchronous client. Usable as a context manager."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, **kwargs)
        self._http = httpx.Client(timeout=self._timeout, transport=transport)

    def __enter__(self) -> BaselithClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        versioned: bool = True,
        json: Any = None,
        params: Any = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        url = self._url(path, versioned=versioned)
        headers = self._headers(idempotency_key)
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.request(
                    method, url, json=json, params=params, headers=headers
                )
            except httpx.HTTPError as e:
                last_exc = e
                if attempt >= self._max_retries:
                    raise APIConnectionError(str(e)) from e
                time.sleep(_backoff_seconds(attempt, None))
                continue
            if resp.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                time.sleep(_backoff_seconds(attempt, retry_after))
                continue
            if resp.status_code >= 400:
                raise error_from_response(
                    resp.status_code,
                    _decode_body(resp),
                    request_id=resp.headers.get("X-Request-ID"),
                    retry_after=_parse_retry_after(resp.headers.get("Retry-After")),
                )
            return resp
        # Unreachable: loop either returns or raises.
        raise APIConnectionError(str(last_exc) if last_exc else "request failed")

    # --- API methods ---
    def chat(self, query: str, **kwargs: Any) -> ChatResponse:
        """Send a query to the agent and return the typed response."""
        req = ChatRequest(query=query, **kwargs)
        resp = self._request("POST", "/chat", json=req.model_dump(exclude_none=True))
        return ChatResponse.model_validate(resp.json())

    def chat_stream(self, query: str, **kwargs: Any) -> Iterator[str]:
        """Stream the agent's answer as text chunks."""
        req = ChatRequest(query=query, **kwargs)
        url = self._url("/chat/stream")
        with self._http.stream(
            "POST",
            url,
            json=req.model_dump(exclude_none=True),
            headers=self._headers(),
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise error_from_response(
                    resp.status_code,
                    _decode_body(resp),
                    request_id=resp.headers.get("X-Request-ID"),
                )
            yield from resp.iter_text()

    def submit_feedback(
        self, *, idempotency_key: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Record feedback on a generated answer."""
        req = FeedbackRequest(**kwargs)
        resp = self._request(
            "POST",
            "/feedback",
            json=req.model_dump(exclude_none=True),
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
        return resp.json()

    def health(self) -> HealthStatus:
        """Liveness probe (unauthenticated, unversioned)."""
        resp = self._request("GET", "/health", versioned=False)
        return HealthStatus.model_validate(resp.json())

    def readiness(self) -> ReadinessStatus:
        """Readiness probe (unauthenticated, unversioned)."""
        resp = self._request("GET", "/health/ready", versioned=False)
        return ReadinessStatus.model_validate(resp.json())


class AsyncBaselithClient(_ClientBase):
    """Asynchronous client. Usable as an async context manager."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, **kwargs)
        self._http = httpx.AsyncClient(timeout=self._timeout, transport=transport)

    async def __aenter__(self) -> AsyncBaselithClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        versioned: bool = True,
        json: Any = None,
        params: Any = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        import asyncio

        url = self._url(path, versioned=versioned)
        headers = self._headers(idempotency_key)
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._http.request(
                    method, url, json=json, params=params, headers=headers
                )
            except httpx.HTTPError as e:
                last_exc = e
                if attempt >= self._max_retries:
                    raise APIConnectionError(str(e)) from e
                await asyncio.sleep(_backoff_seconds(attempt, None))
                continue
            if resp.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                await asyncio.sleep(_backoff_seconds(attempt, retry_after))
                continue
            if resp.status_code >= 400:
                raise error_from_response(
                    resp.status_code,
                    _decode_body(resp),
                    request_id=resp.headers.get("X-Request-ID"),
                    retry_after=_parse_retry_after(resp.headers.get("Retry-After")),
                )
            return resp
        raise APIConnectionError(str(last_exc) if last_exc else "request failed")

    # --- API methods ---
    async def chat(self, query: str, **kwargs: Any) -> ChatResponse:
        """Send a query to the agent and return the typed response."""
        req = ChatRequest(query=query, **kwargs)
        resp = await self._request(
            "POST", "/chat", json=req.model_dump(exclude_none=True)
        )
        return ChatResponse.model_validate(resp.json())

    async def chat_stream(self, query: str, **kwargs: Any) -> AsyncIterator[str]:
        """Stream the agent's answer as text chunks."""
        req = ChatRequest(query=query, **kwargs)
        url = self._url("/chat/stream")
        async with self._http.stream(
            "POST",
            url,
            json=req.model_dump(exclude_none=True),
            headers=self._headers(),
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise error_from_response(
                    resp.status_code,
                    _decode_body(resp),
                    request_id=resp.headers.get("X-Request-ID"),
                )
            async for chunk in resp.aiter_text():
                yield chunk

    async def submit_feedback(
        self, *, idempotency_key: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Record feedback on a generated answer."""
        req = FeedbackRequest(**kwargs)
        resp = await self._request(
            "POST",
            "/feedback",
            json=req.model_dump(exclude_none=True),
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
        return resp.json()

    async def health(self) -> HealthStatus:
        """Liveness probe (unauthenticated, unversioned)."""
        resp = await self._request("GET", "/health", versioned=False)
        return HealthStatus.model_validate(resp.json())

    async def readiness(self) -> ReadinessStatus:
        """Readiness probe (unauthenticated, unversioned)."""
        resp = await self._request("GET", "/health/ready", versioned=False)
        return ReadinessStatus.model_validate(resp.json())
