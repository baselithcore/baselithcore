"""
HTTP RED-metrics middleware (pure ASGI).

Emits the three golden HTTP-server signals — **R**ate, **E**rrors, **D**uration —
as Prometheus series on the default registry scraped by ``/metrics``:

- ``http_requests_total{method, route, status}``      (rate + errors)
- ``http_request_duration_seconds{method, route}``    (duration histogram)
- ``http_requests_in_progress{method, route}``        (saturation gauge)

**Cardinality safety.** The ``route`` label is the matched path *template*
(e.g. ``/api/plugins/{plugin_name}``) taken from ``scope["route"].path_format``
(FastAPI injects the matched route into the scope) — never the raw URL — so
per-id paths collapse to one series. Plain Starlette routes that do not inject
``scope["route"]`` fall back to a reconstruction from ``scope["path_params"]``.
Unmatched requests (404 probes, scanners) bucket under ``__unmatched__`` and
unusual verbs under ``OTHER``, keeping the label set bounded regardless of
traffic. Written as pure ASGI (never ``BaseHTTPMiddleware``) so it does not break
streaming or cancellation.
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.observability.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_IN_PROGRESS,
    HTTP_REQUESTS_TOTAL,
)

# Standard verbs kept as-is; anything else collapses to OTHER (bounded label).
_KNOWN_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}
)

# Paths excluded from instrumentation: the scrape endpoint itself (avoid the
# self-observation feedback loop) and static assets (noise, no SLO value).
_EXCLUDED_PREFIXES = ("/metrics", "/v1/metrics", "/static")


def _route_template(scope: Scope) -> str:
    """Return a low-cardinality route label for ``scope``.

    Prefers the matched route's compiled ``path_format`` (FastAPI sets
    ``scope["route"]`` on match); falls back to rebuilding the template from
    ``path_params`` for plain Starlette routes, and to ``__unmatched__`` when
    no endpoint matched (so 404 scans cannot explode the label set).
    """
    if scope.get("endpoint") is None:
        return "__unmatched__"
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    if template:
        return str(template)
    # Plain Starlette routes don't inject scope["route"]: rebuild the template
    # from path_params. String replacement can mangle a value that repeats an
    # earlier path token, but these routes are static/param-free in practice.
    path = scope.get("path") or "/"
    params = scope.get("path_params") or {}
    for name, value in params.items():
        text = str(value)
        if text:
            path = path.replace(text, "{" + name + "}", 1)
    return path


class HTTPMetricsMiddleware:
    """Pure ASGI middleware recording per-request RED metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_path = scope.get("path") or "/"
        if raw_path.startswith(_EXCLUDED_PREFIXES):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if method not in _KNOWN_METHODS:
            method = "OTHER"

        status_holder = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = int(message.get("status", 500))
            await send(message)

        start = time.perf_counter()
        # Saturation gauge is labelled by method only: the route template is
        # unknown until after routing, and a per-method in-flight count is the
        # standard saturation signal.
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method).inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            HTTP_REQUESTS_IN_PROGRESS.labels(method=method).dec()
            route = _route_template(scope)
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, route=route).observe(
                elapsed
            )
            HTTP_REQUESTS_TOTAL.labels(
                method=method, route=route, status=str(status_holder["code"])
            ).inc()


__all__ = ["HTTPMetricsMiddleware"]
