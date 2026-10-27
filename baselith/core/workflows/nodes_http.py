"""HTTP request node.

Outbound calls from a workflow are attacker-influenced: the definition may be
uploaded through the API and the URL is usually built from model output or a
webhook payload. The node therefore goes through the same egress screening as
every other outbound fetch in the framework (see
:mod:`baselith.core.security.ssrf`): scheme and host checks, DNS resolution,
and a connection pinned to the address that was vetted. Redirects are not
followed and keep-alive is off, so a 3xx cannot steer the call to an internal
host and a pooled TLS session is never reused across hostnames.

Credentials never live in a definition. A node names a credential
(``config['credential']``) and the operator maps that name to headers when
building the executor (``services={"http_credentials": {...}}``).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr

from baselith.core.security.ssrf import SsrfPolicy, resolve_pinned_target

from ._scope import build_scope, render_value
from .builder import WorkflowNode

if TYPE_CHECKING:
    from .executor import ExecutionContext, WorkflowExecutor

__all__ = ["handle_http"]

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 120.0
_DEFAULT_MAX_BYTES = 1_048_576
_CEILING_MAX_BYTES = 10 * 1_048_576

#: Headers a definition may not set: they would change framing or routing.
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "upgrade",
        "te",
        "trailer",
        "proxy-authorization",
        "proxy-connection",
    }
)


def _clean_headers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("HTTP node headers must be a mapping")
    return {
        str(k): str(v)
        for k, v in raw.items()
        if str(k).lower() not in _RESERVED_HEADERS
    }


def _credential_headers(
    executor: WorkflowExecutor, node: WorkflowNode
) -> dict[str, str]:
    name = node.config.get("credential")
    if not name:
        return {}
    registry = executor._services.get("http_credentials") or {}
    entry = registry.get(name)
    if entry is None:
        raise ValueError(
            f"HTTP node {node.id!r}: credential {name!r} is not configured on the executor"
        )
    return _clean_headers(
        {
            k: v.get_secret_value() if isinstance(v, SecretStr) else v
            for k, v in entry.items()
        }
    )


def _policy() -> SsrfPolicy:
    from baselith.core.config.workflows import get_workflows_config

    return SsrfPolicy(allow_internal=get_workflows_config().http_allow_internal)


async def _read_capped(response: httpx.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise RuntimeError(f"HTTP response larger than {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_body(content_type: str, raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower() and text.strip():
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


async def handle_http(
    executor: WorkflowExecutor, node: WorkflowNode, context: ExecutionContext
) -> Any:
    """Call a remote HTTP endpoint and return its response.

    Config:
        url: Target URL (templated). ``http`` and ``https`` only.
        method: GET (default), POST, PUT, PATCH or DELETE.
        headers: Extra request headers (templated; framing headers dropped).
        query: Query parameters (templated).
        json: JSON request body (templated, types preserved).
        body: Raw text body (templated); ignored when ``json`` is set.
        credential: Name of an operator-configured credential.
        timeout: Seconds, at most 120 (default 30).
        max_response_bytes: Response cap, at most 10 MiB (default 1 MiB).
        allow_error_status: Return 4xx/5xx instead of failing the node.

    Returns:
        ``{"status", "ok", "content_type", "body"}``; ``body`` is parsed JSON
        for JSON responses and text otherwise.

    Raises:
        ValueError: Invalid configuration.
        SsrfError: The target is not a permitted outbound destination.
        RuntimeError: Non-2xx status (unless allowed) or an oversized body.
    """
    config = node.config
    scope = build_scope(context)
    method = str(config.get("method", "GET")).upper()
    if method not in _METHODS:
        raise ValueError(f"HTTP node {node.id!r}: unsupported method {method!r}")
    url = render_value(config.get("url"), scope)
    if not isinstance(url, str) or not url:
        raise ValueError(f"HTTP node {node.id!r} needs a config['url']")

    headers = {
        **_clean_headers(render_value(config.get("headers") or {}, scope)),
        **_credential_headers(executor, node),
    }
    params = render_value(config.get("query") or {}, scope)
    timeout = min(float(config.get("timeout", _DEFAULT_TIMEOUT)), _MAX_TIMEOUT)
    limit = min(
        int(config.get("max_response_bytes", _DEFAULT_MAX_BYTES)), _CEILING_MAX_BYTES
    )
    request: dict[str, Any] = {"params": params}
    if config.get("json") is not None:
        request["json"] = render_value(config["json"], scope)
    elif config.get("body") is not None:
        request["content"] = str(render_value(config["body"], scope)).encode("utf-8")

    pinned_url, host = await asyncio.to_thread(resolve_pinned_target, url, _policy())
    client = executor._services.get("http_client")
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
    try:
        async with client.stream(
            method,
            pinned_url,
            headers={**headers, "Host": host},
            extensions={"sni_hostname": host},
            **request,
        ) as response:
            raw = await _read_capped(response, limit)
            status = response.status_code
            content_type = response.headers.get("content-type", "")
    finally:
        if owns_client:
            await client.aclose()

    ok = 200 <= status < 300
    if not ok and not config.get("allow_error_status"):
        raise RuntimeError(f"HTTP node {node.id!r}: remote answered {status}")
    return {
        "status": status,
        "ok": ok,
        "content_type": content_type,
        "body": _decode_body(content_type, raw),
    }
