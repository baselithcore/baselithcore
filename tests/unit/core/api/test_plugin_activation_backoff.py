"""A lazy plugin that fails to activate is not retried on every request.

Regression: activation is triggered by any (even anonymous) request to the
plugin's prefix, and a failure was not remembered — each request re-hashed and
re-imported the plugin under the global activation lock. A failure now backs
off: requests get ``503`` + ``Retry-After`` without a new attempt, and an
operator enable (which fires the activation hook) lifts it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from core.api._plugin_runtime import PluginRuntimeHooks
from core.middleware.plugin_activation import PluginActivationMiddleware
from core.plugins import PluginState
from core.plugins._activation_backoff import (
    ActivationBackoff,
    PluginActivationBackoffError,
)


class _Lifecycle:
    def __init__(self) -> None:
        self.states: dict[str, PluginState] = {}

    def get_state(self, name: str) -> PluginState:
        return self.states.get(name, PluginState.DISCOVERED)


class _Registry:
    def get_discovered_plugin(self, name: str) -> Any:
        meta = SimpleNamespace(plugin_dependencies={})
        return SimpleNamespace(metadata=meta, name=name, directory_name=name)


class _FailingHotReload:
    def __init__(self, lifecycle: _Lifecycle) -> None:
        self._lifecycle = lifecycle
        self.attempts = 0
        self.succeed = False

    async def enable_plugin(self, name: str, _config: dict[str, Any]) -> bool:
        self.attempts += 1
        if self.succeed:
            self._lifecycle.states[name] = PluginState.ACTIVE
        return self.succeed


def _hooks() -> tuple[PluginRuntimeHooks, _FailingHotReload]:
    lifecycle = _Lifecycle()
    hot = _FailingHotReload(lifecycle)
    hooks = PluginRuntimeHooks(
        app=SimpleNamespace(state=SimpleNamespace()),  # type: ignore[arg-type]
        plugin_registry=_Registry(),
        plugin_configs={},
        lifecycle_manager=lifecycle,
        hot_reload_controller=hot,
    )
    return hooks, hot


@pytest.mark.asyncio
async def test_failed_activation_is_not_retried_within_the_backoff() -> None:
    hooks, hot = _hooks()
    assert await hooks.activate_plugin_for_runtime("broken") is False
    for _ in range(5):
        with pytest.raises(PluginActivationBackoffError) as ei:
            await hooks.activate_plugin_for_runtime("broken")
        assert 1 <= ei.value.retry_after <= 60
    assert hot.attempts == 1


@pytest.mark.asyncio
async def test_activation_raising_also_backs_off() -> None:
    hooks, hot = _hooks()

    async def boom(name: str, _config: dict[str, Any]) -> bool:
        hot.attempts += 1
        raise ImportError("broken plugin")

    hot.enable_plugin = boom  # type: ignore[method-assign]
    with pytest.raises(ImportError):
        await hooks.activate_plugin_for_runtime("broken")
    with pytest.raises(PluginActivationBackoffError):
        await hooks.activate_plugin_for_runtime("broken")
    assert hot.attempts == 1


@pytest.mark.asyncio
async def test_backoff_expires() -> None:
    hooks, hot = _hooks()
    hooks.activation_backoff = ActivationBackoff(seconds=0)
    await hooks.activate_plugin_for_runtime("broken")
    await hooks.activate_plugin_for_runtime("broken")
    assert hot.attempts == 2


@pytest.mark.asyncio
async def test_operator_enable_lifts_the_backoff() -> None:
    hooks, hot = _hooks()
    await hooks.activate_plugin_for_runtime("broken")
    # The operator fixes the plugin and enables it through the management API,
    # which goes to the hot-reload controller directly and then fires the
    # runtime activation hook.
    hooks._lifecycle.states["broken"] = PluginState.ACTIVE
    plugin = SimpleNamespace(
        metadata=SimpleNamespace(name="broken"),
        get_router_prefix=lambda: "/broken",
        get_routers=lambda: [],
    )
    hooks._registry.get_all_static_paths = lambda: {}  # type: ignore[attr-defined]
    await hooks.on_plugin_activated(plugin)
    hooks.activation_backoff.check("broken")  # no longer raises
    # And once active, lazy activation succeeds without a new attempt.
    assert await hooks.activate_plugin_for_runtime("broken") is True
    assert hot.attempts == 1


class _MiddlewareRegistry:
    """Plugin registry stand-in whose activation goes through real hooks."""

    def __init__(self, hooks: PluginRuntimeHooks) -> None:
        self._hooks = hooks

    def match_plugin_route(self, path: str) -> str | None:
        return "broken" if path.startswith("/broken") else None

    async def ensure_plugin_active(self, name: str) -> bool:
        return await self._hooks.activate_plugin_for_runtime(name)


def test_middleware_answers_503_retry_after_without_reattempting() -> None:
    hooks, hot = _hooks()
    inner = Starlette(routes=[Route("/broken/x", lambda r: PlainTextResponse("ok"))])
    inner.state.plugin_registry = _MiddlewareRegistry(hooks)

    async def app(scope: Any, receive: Any, send: Any) -> None:
        scope["app"] = inner
        await PluginActivationMiddleware(inner)(scope, receive, send)

    client = TestClient(app)
    first = client.get("/broken/x")
    assert first.status_code == 503
    assert first.headers["retry-after"] == "60"
    for _ in range(3):
        again = client.get("/broken/x")
        assert again.status_code == 503
        assert 1 <= int(again.headers["retry-after"]) <= 60
    assert hot.attempts == 1
