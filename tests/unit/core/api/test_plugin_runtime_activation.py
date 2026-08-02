"""Transitive dependency auto-activation in :class:`PluginRuntimeHooks`.

Regression: lazy runtime activation enabled a plugin's *direct* dependency but
not that dependency's *own* dependencies. A plugin depending on ``resto-graph``
(which depends on the ``document-sources`` infra plugin) failed with
"requires document-sources which is not loaded", because ``resto-graph`` was
enabled while ``document-sources`` was still dormant. Activation must recurse so
transitive infra deps come up first.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.api._plugin_runtime import PluginRuntimeHooks
from core.plugins import PluginState


class _Lifecycle:
    """Tracks per-plugin state; starts everything dormant (DISCOVERED)."""

    def __init__(self) -> None:
        self.states: dict[str, PluginState] = {}

    def get_state(self, name: str) -> PluginState:
        return self.states.get(name, PluginState.DISCOVERED)


class _Registry:
    def __init__(self, graph: dict[str, dict[str, str]]) -> None:
        self._graph = graph

    def get_discovered_plugin(self, name: str) -> Any:
        if name not in self._graph:
            return None
        meta = SimpleNamespace(plugin_dependencies=self._graph[name])
        return SimpleNamespace(
            metadata=meta, name=name, directory_name=name.replace("-", "_")
        )


class _HotReload:
    """Records enable order; flips lifecycle state to ACTIVE on enable."""

    def __init__(self, lifecycle: _Lifecycle) -> None:
        self._lifecycle = lifecycle
        self.enabled: list[str] = []

    async def enable_plugin(self, name: str, _config: dict[str, Any]) -> bool:
        self.enabled.append(name)
        self._lifecycle.states[name] = PluginState.ACTIVE
        return True


def _hooks(graph: dict[str, dict[str, str]]) -> tuple[PluginRuntimeHooks, _HotReload]:
    lifecycle = _Lifecycle()
    hot = _HotReload(lifecycle)
    hooks = PluginRuntimeHooks(
        app=SimpleNamespace(),  # type: ignore[arg-type]  # unused by activation path
        plugin_registry=_Registry(graph),
        plugin_configs={},
        lifecycle_manager=lifecycle,
        hot_reload_controller=hot,
    )
    return hooks, hot


@pytest.mark.asyncio
async def test_transitive_dependency_activation_order() -> None:
    # resto-service -> resto-graph -> document-sources
    graph = {
        "resto-service": {"resto-graph": ">=1.0.0"},
        "resto-graph": {"document-sources": ">=1.0.0"},
        "document-sources": {},
    }
    hooks, hot = _hooks(graph)

    assert await hooks.activate_plugin_for_runtime("resto-service") is True
    # Dependency enabled before dependent, all the way down the chain.
    assert hot.enabled == ["document-sources", "resto-graph", "resto-service"]


@pytest.mark.asyncio
async def test_already_active_dependency_not_reenabled() -> None:
    graph = {
        "resto-graph": {"document-sources": ">=1.0.0"},
        "document-sources": {},
    }
    hooks, hot = _hooks(graph)
    hooks._lifecycle.states["document-sources"] = PluginState.ACTIVE

    assert await hooks.activate_plugin_for_runtime("resto-graph") is True
    assert hot.enabled == ["resto-graph"]  # doc-sources already up, not re-enabled


@pytest.mark.asyncio
async def test_dependency_cycle_does_not_recurse_forever() -> None:
    graph = {"a": {"b": "*"}, "b": {"a": "*"}}
    hooks, hot = _hooks(graph)

    # Must terminate (cycle guard) rather than hang / RecursionError.
    assert await hooks.activate_plugin_for_runtime("a") is True
    assert set(hot.enabled) == {"a", "b"}


@pytest.mark.asyncio
async def test_disabled_dependency_is_not_auto_activated() -> None:
    # ``secret-plugin`` is absent from discovery (operator set ``enabled:
    # false``, so ``discover_plugins`` skipped it). Activating a dependent must
    # fail closed and NEVER enable the disabled dependency.
    graph = {"resto-service": {"secret-plugin": ">=1.0.0"}}  # secret-plugin absent
    hooks, hot = _hooks(graph)

    assert await hooks.activate_plugin_for_runtime("resto-service") is False
    assert "secret-plugin" not in hot.enabled
    assert hot.enabled == []  # dependent not enabled either — dep failed


@pytest.mark.asyncio
async def test_transitive_disabled_dependency_fails_closed() -> None:
    # resto-service -> resto-graph -> document-sources(disabled/absent).
    # The whole chain must refuse: no plugin in the chain gets enabled.
    graph = {
        "resto-service": {"resto-graph": ">=1.0.0"},
        "resto-graph": {"document-sources": ">=1.0.0"},
        # document-sources intentionally absent from discovery
    }
    hooks, hot = _hooks(graph)

    assert await hooks.activate_plugin_for_runtime("resto-service") is False
    assert hot.enabled == []


@pytest.mark.asyncio
async def test_undiscovered_plugin_activation_refused() -> None:
    hooks, hot = _hooks({})  # nothing discovered
    assert await hooks.activate_plugin_for_runtime("ghost") is False
    assert hot.enabled == []


class _RecordingApp:
    def __init__(self) -> None:
        self.mounts: list[str] = []

    def mount(self, path: str, app: Any, name: str = "") -> None:
        self.mounts.append(path)


def test_invalid_plugin_name_not_mounted(tmp_path: Any) -> None:
    app = _RecordingApp()
    hooks = PluginRuntimeHooks(
        app=app,  # type: ignore[arg-type]
        plugin_registry=_Registry({}),
        plugin_configs={},
        lifecycle_manager=_Lifecycle(),
        hot_reload_controller=_HotReload(_Lifecycle()),
    )
    (tmp_path / "index.html").write_text("<html></html>")

    # A name carrying a path separator must never reach ``app.mount``.
    hooks.mount_plugin_static("evil/../../admin", tmp_path)
    assert app.mounts == []

    # A valid slug mounts normally (static + SPA index present).
    hooks.mount_plugin_static("good-plugin", tmp_path)
    assert "/plugins/good-plugin/static" in app.mounts
