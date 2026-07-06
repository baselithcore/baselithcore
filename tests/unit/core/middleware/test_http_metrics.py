"""
Tests for core/middleware/http_metrics.py.

Focus: the ``route`` label must always be the matched path *template*
(bounded cardinality), never the raw URL — including when a path-param
value collides with an earlier static path segment.
"""

from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY

from core.middleware.http_metrics import HTTPMetricsMiddleware, _route_template


def _scope(path: str, **extra) -> dict:
    scope = {"type": "http", "method": "GET", "path": path}
    scope.update(extra)
    return scope


class TestRouteTemplate:
    def test_prefers_matched_route_path_format(self):
        route = SimpleNamespace(path_format="/api/v1/users/{user_id}")
        scope = _scope(
            "/api/v1/users/123",
            endpoint=object(),
            route=route,
            path_params={"user_id": "123"},
        )
        assert _route_template(scope) == "/api/v1/users/{user_id}"

    def test_param_value_colliding_with_static_segment(self):
        # Regression: the old string-replace reconstruction rewrote the wrong
        # occurrence for id "1" on /api/v1/... ("/api/v{user_id}/users/1"),
        # leaking the raw id into the label.
        route = SimpleNamespace(path_format="/api/v1/users/{user_id}")
        scope = _scope(
            "/api/v1/users/1",
            endpoint=object(),
            route=route,
            path_params={"user_id": "1"},
        )
        assert _route_template(scope) == "/api/v1/users/{user_id}"

    def test_fallback_reconstruction_without_route_object(self):
        # Plain Starlette routes don't inject scope["route"].
        scope = _scope(
            "/api/plugins/demo",
            endpoint=object(),
            path_params={"plugin_name": "demo"},
        )
        assert _route_template(scope) == "/api/plugins/{plugin_name}"

    def test_unmatched_paths_bucket_together(self):
        assert _route_template(_scope("/definitely/not/a/route")) == "__unmatched__"


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_records_template_label_end_to_end(self):
        route = SimpleNamespace(path_format="/api/items/{item_id}")

        async def app(scope, receive, send):
            # Simulate the router having matched an APIRoute.
            scope["endpoint"] = object()
            scope["route"] = route
            scope["path_params"] = {"item_id": "42"}
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request"}

        labels = {"method": "GET", "route": "/api/items/{item_id}", "status": "200"}
        before = REGISTRY.get_sample_value("http_requests_total", labels) or 0.0

        await HTTPMetricsMiddleware(app)(_scope("/api/items/42"), receive, send)

        after = REGISTRY.get_sample_value("http_requests_total", labels) or 0.0
        assert after == before + 1
        assert sent[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_excluded_paths_not_instrumented(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        labels = {"method": "GET", "route": "/metrics", "status": "200"}
        before = REGISTRY.get_sample_value("http_requests_total", labels) or 0.0

        async def send(message):
            pass

        async def receive():
            return {"type": "http.request"}

        await HTTPMetricsMiddleware(app)(_scope("/metrics"), receive, send)

        after = REGISTRY.get_sample_value("http_requests_total", labels) or 0.0
        assert after == before
