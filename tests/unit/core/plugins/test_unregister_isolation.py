"""A plugin cannot refuse to be unregistered.

``PluginRegistry.unregister`` unwires the plugin's components, cancels its
background tasks, calls ``shutdown()`` and only then deletes the registry entry.
The ``shutdown()`` call was not isolated, so a plugin whose shutdown raised
aborted the sequence *after* its components were already gone: the name stayed
claimed by a plugin that served nothing, still appeared in listings and health
checks, and could not be re-registered — which is also what a reload needs to do
next.

The sharp case is not hypothetical. The nursery deliberately refuses new
background work while teardown is in flight (``PluginTaskClosedError``), and a
plugin that spawns a task from inside ``shutdown()`` without catching it hands
that refusal straight to the registry. ``hotreload._do_disable`` already wraps
its own call this way.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from core.plugins import PluginRegistry
from core.plugins.interface import Plugin, PluginMetadata
from core.plugins.nursery import PluginTaskClosedError

pytestmark = [pytest.mark.unit]


class _StubPlugin(Plugin):
    def __init__(self, name: str = "stub", version: str = "1.0.0") -> None:
        super().__init__()
        self._metadata = PluginMetadata(name=name, version=version)

    @property
    def metadata(self) -> PluginMetadata:  # type: ignore[override]
        return self._metadata

    async def initialize(self, config=None) -> None:
        self._config = config or {}
        self._initialized = True


class _SpawningShutdown(_StubPlugin):
    """Spawns background work from ``shutdown()`` and does not catch the refusal."""

    def __init__(self, registry: PluginRegistry) -> None:
        super().__init__(name="spawner")
        self._registry = registry

    async def shutdown(self) -> None:
        async def _late_work() -> None:  # pragma: no cover - never scheduled
            await asyncio.sleep(60)

        self._registry.spawn_task("spawner", _late_work())


class _RaisingShutdown(_StubPlugin):
    async def shutdown(self) -> None:
        raise RuntimeError("teardown exploded")


class _CancelledShutdown(_StubPlugin):
    async def shutdown(self) -> None:
        raise asyncio.CancelledError


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry()


async def _register(registry: PluginRegistry, plugin: Plugin) -> Plugin:
    await plugin.initialize({})
    registry.register(plugin)
    return plugin


class TestShutdownIsIsolated:
    async def test_a_refused_spawn_does_not_keep_the_plugin_registered(self, registry):
        plugin = _SpawningShutdown(registry)
        await _register(registry, plugin)

        await registry.unregister("spawner")

        assert registry.get("spawner") is None
        assert "spawner" not in registry

    async def test_the_refusal_is_still_the_nursery_contract(self, registry):
        """Sanity: the spawn really is refused during teardown, so the test
        above is exercising the isolation and not a nursery that went quiet."""
        await _register(registry, _StubPlugin(name="spawner"))

        async def _work() -> None:  # pragma: no cover - never scheduled
            await asyncio.sleep(60)

        with registry._nursery.closing("spawner"):
            with pytest.raises(PluginTaskClosedError):
                registry.spawn_task("spawner", _work())

    async def test_any_shutdown_failure_is_logged_and_swallowed(self, registry):
        """Swallowed, never silent: the failure has to reach an operator."""
        import core.plugins.registry as registry_module

        await _register(registry, _RaisingShutdown())

        with patch.object(registry_module, "logger") as log:
            await registry.unregister("stub")

        assert registry.get("stub") is None
        log.error.assert_called_once()
        assert "shutdown" in log.error.call_args.args[0].lower()
        assert log.error.call_args.kwargs.get("exc_info") is True

    async def test_cancellation_still_propagates(self, registry):
        """``CancelledError`` is not an error the plugin owns — swallowing it
        would make the teardown unkillable."""
        await _register(registry, _CancelledShutdown())

        with pytest.raises(asyncio.CancelledError):
            await registry.unregister("stub")

    async def test_a_clean_shutdown_is_unchanged(self, registry):
        calls: list[str] = []

        class _Clean(_StubPlugin):
            async def shutdown(self) -> None:
                calls.append("shutdown")

        await _register(registry, _Clean())
        await registry.unregister("stub")

        assert calls == ["shutdown"]
        assert registry.get("stub") is None
