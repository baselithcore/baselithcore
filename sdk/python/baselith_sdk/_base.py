"""Transport plumbing shared by the sync and async clients.

Split out of ``client.py`` so that module holds only the API methods (the
``sdk-contract`` gate reads the routes from it): configuration, URL and header
construction, path-parameter substitution, the retrying ``_request`` loop
for each flavour, the SSE body readers, context managers and ``iter_pages``.

Retry policy is per call. A *safe* call (reads, and writes the server dedupes
by ``Idempotency-Key`` cheaply) is retried on transport errors, ``429`` and
``5xx``. An *unsafe* call (``retry_unsafe=False``: approval decisions, run
resumes, delivery replays — long or side-effecting handlers) is retried only
when the request provably never reached the server (connect/pool errors) or
was rate-limited (``429``); a read timeout or a ``5xx`` may mean the handler
is still running, so it surfaces to the caller instead of being re-sent.
"""

from __future__ import annotations

import asyncio
import base64
import random
import time
import uuid
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
    TypeVar,
)
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from . import _pagination
from .errors import APIConnectionError, BaselithConfigError, error_from_response
from .models import Page
from .version import __version__

P = TypeVar("P", bound=Page)
_S = TypeVar("_S", bound="_SyncBase")
_A = TypeVar("_A", bound="_AsyncBase")

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 2
#: Default read timeout of an SSE stream: the gap allowed between two frames.
#: Four times the server's default ``SSE_HEARTBEAT_SECONDS`` (15 s), so a quiet
#: but healthy stream is never cut by the 30 s request timeout.
_DEFAULT_STREAM_READ_TIMEOUT = 60.0
#: Default timeout of ``resume_run``: the server runs the resumed agent loop
#: in-request (bounded at ~600 s), plus headroom.
_DEFAULT_RESUME_TIMEOUT = 660.0
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
#: Statuses an unsafe call may still retry: the request was refused up front.
_UNSAFE_RETRYABLE_STATUS = frozenset({429})
#: Transport errors raised before any byte of the request left the client.
_NOT_SENT_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)
_USER_AGENT = f"baselith-sdk-python/{__version__}"


def _build_headers(
    api_key: str | None,
    bearer_token: str | None,
    tenant_id: str | None,
    basic_auth: tuple[str, str] | None = None,
) -> dict[str, str]:
    """Assemble the static default headers for every request."""
    if bearer_token and basic_auth:
        raise BaselithConfigError(
            "bearer_token and basic_auth both set the Authorization header; pass one"
        )
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if basic_auth:
        token = base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
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
    ctype = response.headers.get("content-type", "").lower()
    # ``application/json`` and every ``+json`` structured suffix — above all the
    # RFC 9457 ``application/problem+json`` the server answers errors with,
    # which a plain ``"application/json" in ctype`` test does not match.
    if "application/json" in ctype or "+json" in ctype:
        try:
            return response.json()
        except Exception:
            return response.text
    return response.text


def _raise_for_status(resp: httpx.Response) -> None:
    """Raise the typed SDK error for an error response (body already read)."""
    if resp.status_code >= 400:
        raise error_from_response(
            resp.status_code,
            _decode_body(resp),
            request_id=resp.headers.get("X-Request-ID"),
            retry_after=_parse_retry_after(resp.headers.get("Retry-After")),
        )


def _sse_body(resp: httpx.Response) -> Iterator[str]:
    """An open SSE response's text, or the typed error for an error status."""
    if resp.status_code >= 400:
        resp.read()
        _raise_for_status(resp)
    return resp.iter_text()


async def _asse_body(resp: httpx.Response) -> AsyncIterator[str]:
    """Async twin of :func:`_sse_body`."""
    if resp.status_code >= 400:
        await resp.aread()
        _raise_for_status(resp)
    return resp.aiter_text()


def _fill_path(path: str, path_params: Mapping[str, Any] | None) -> str:
    """Substitute ``{name}`` placeholders, percent-encoding every value.

    Routes are written as their OpenAPI templates (``/agent/status/{task_id}``)
    so the ``sdk-contract`` gate can match them against the spec; an id with a
    ``/`` or ``?`` in it must not be able to reach a different route.
    """
    if not path_params:
        return path
    for name, value in path_params.items():
        path = path.replace("{" + name + "}", quote(str(value), safe=""))
    return path


def _json_body(json: Any) -> Any:
    """A request model is sent as its non-``None`` fields; anything else as is."""
    if isinstance(json, BaseModel):
        return json.model_dump(exclude_none=True)
    return json


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Drop ``None`` query values so optional filters are simply omitted."""
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None} or None


class _ClientBase:
    """Shared configuration and URL/header construction for both clients."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        bearer_token: str | None = None,
        basic_auth: tuple[str, str] | None = None,
        tenant_id: str | None = None,
        api_version: str | None = "v1",
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        stream_read_timeout: float | None = _DEFAULT_STREAM_READ_TIMEOUT,
    ) -> None:
        if not base_url:
            raise BaselithConfigError("base_url is required")
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version.strip("/") if api_version else None
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        self._stream_timeout = httpx.Timeout(timeout, read=stream_read_timeout)
        self._default_headers = _build_headers(
            api_key, bearer_token, tenant_id, basic_auth
        )

    def _url(
        self,
        path: str,
        *,
        versioned: bool = True,
        path_params: Mapping[str, Any] | None = None,
    ) -> str:
        path = "/" + _fill_path(path, path_params).lstrip("/")
        if versioned and self._api_version:
            return f"{self._base_url}/{self._api_version}{path}"
        return f"{self._base_url}{path}"

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = dict(self._default_headers)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _stream_headers(self, last_event_id: str | None = None) -> dict[str, str]:
        headers = dict(self._default_headers, Accept="text/event-stream")
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        return headers

    def _stream_kwargs(
        self, headers: dict[str, str], json: Any = None
    ) -> dict[str, Any]:
        """``httpx`` stream kwargs: the SSE read timeout, plus an optional body."""
        kwargs: dict[str, Any] = {"headers": headers, "timeout": self._stream_timeout}
        if json is not None:
            kwargs["json"] = _json_body(json)
        return kwargs

    @staticmethod
    def _unsafe_call(idempotency_key: str | None) -> dict[str, Any]:
        """``_request`` kwargs of a non-replayable POST (approval decision, run
        resume, delivery replay): an ``Idempotency-Key`` (auto-generated unless
        given) and no re-send after a timeout or ``5xx``."""
        key = idempotency_key or str(uuid.uuid4())
        return {"idempotency_key": key, "retry_unsafe": False}

    def _exc_retry_delay(
        self, attempt: int, exc: Exception, retry_unsafe: bool
    ) -> float | None:
        """Backoff before retrying after ``exc``, or ``None`` to give up."""
        if attempt >= self._max_retries:
            return None
        if not retry_unsafe and not isinstance(exc, _NOT_SENT_ERRORS):
            return None
        return _backoff_seconds(attempt, None)

    def _status_retry_delay(
        self, attempt: int, resp: httpx.Response, retry_unsafe: bool
    ) -> float | None:
        """Backoff before retrying after ``resp``, or ``None`` to return/raise it."""
        statuses = _RETRYABLE_STATUS if retry_unsafe else _UNSAFE_RETRYABLE_STATUS
        if attempt >= self._max_retries or resp.status_code not in statuses:
            return None
        return _backoff_seconds(
            attempt, _parse_retry_after(resp.headers.get("Retry-After"))
        )


class _SyncBase(_ClientBase):
    """Synchronous transport: an ``httpx.Client`` plus the retry loop."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, **kwargs)
        self._http = httpx.Client(timeout=self._timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self: _S) -> _S:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def iter_pages(
        self, fetch: Callable[..., P], /, *args: Any, **kwargs: Any
    ) -> Iterator[P]:
        """Yield every page of ``fetch`` (e.g. ``self.list_webhooks``)."""
        return _pagination.iter_pages(fetch, *args, **kwargs)

    def _request(
        self,
        method: str,
        path: str,
        *,
        versioned: bool = True,
        path_params: Mapping[str, Any] | None = None,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        retry_unsafe: bool = True,
        timeout: float | None = None,
    ) -> httpx.Response:
        url = self._url(path, versioned=versioned, path_params=path_params)
        headers = self._headers(idempotency_key)
        query = _clean_params(params)
        json = _json_body(json)
        per_call: Any = httpx.USE_CLIENT_DEFAULT if timeout is None else timeout
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.request(
                    method,
                    url,
                    json=json,
                    params=query,
                    headers=headers,
                    timeout=per_call,
                )
            except httpx.HTTPError as e:
                delay = self._exc_retry_delay(attempt, e, retry_unsafe)
                if delay is None:
                    raise APIConnectionError(str(e)) from e
                time.sleep(delay)
                continue
            delay = self._status_retry_delay(attempt, resp, retry_unsafe)
            if delay is not None:
                time.sleep(delay)
                continue
            _raise_for_status(resp)
            return resp
        # Unreachable: the last attempt either returns or raises.
        raise APIConnectionError("request failed")  # pragma: no cover


class _AsyncBase(_ClientBase):
    """Asynchronous transport: an ``httpx.AsyncClient`` plus the retry loop."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, **kwargs)
        self._http = httpx.AsyncClient(timeout=self._timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self: _A) -> _A:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def iter_pages(
        self, fetch: Callable[..., Awaitable[P]], /, *args: Any, **kwargs: Any
    ) -> AsyncIterator[P]:
        """Async-iterate every page of ``fetch`` (e.g. ``self.list_webhooks``)."""
        return _pagination.aiter_pages(fetch, *args, **kwargs)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        versioned: bool = True,
        path_params: Mapping[str, Any] | None = None,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        retry_unsafe: bool = True,
        timeout: float | None = None,
    ) -> httpx.Response:
        url = self._url(path, versioned=versioned, path_params=path_params)
        headers = self._headers(idempotency_key)
        query = _clean_params(params)
        json = _json_body(json)
        per_call: Any = httpx.USE_CLIENT_DEFAULT if timeout is None else timeout
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._http.request(
                    method,
                    url,
                    json=json,
                    params=query,
                    headers=headers,
                    timeout=per_call,
                )
            except httpx.HTTPError as e:
                delay = self._exc_retry_delay(attempt, e, retry_unsafe)
                if delay is None:
                    raise APIConnectionError(str(e)) from e
                await asyncio.sleep(delay)
                continue
            delay = self._status_retry_delay(attempt, resp, retry_unsafe)
            if delay is not None:
                await asyncio.sleep(delay)
                continue
            _raise_for_status(resp)
            return resp
        raise APIConnectionError("request failed")  # pragma: no cover
