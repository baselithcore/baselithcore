"""Per-plugin task accounting and the optional ``health()`` hook.

A plugin that spawns background work (a poller, a cron loop, a websocket pump)
had nowhere to register it, so a reload left the old tasks running against the
old code — two generations of the same plugin, both live. The registry now owns
a nursery per plugin whose tasks are cancelled and awaited on unregister and on
reload.

The health hook is the reporting half: a plugin can say more than "is
``initialize`` done", and the registry surfaces it when the plugin overrides it.
"""

from __future__ import annotations

import asyncio

import pytest

from core.plugins import PluginRegistry
from core.plugins.health import PluginHealth
from core.plugins.interface import Plugin, PluginMetadata


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


class _HealthyPlugin(_StubPlugin):
    async def health(self) -> PluginHealth:
        return PluginHealth(healthy=True, detail="all good", data={"queue": 0})


class _SickPlugin(_StubPlugin):
    async def health(self) -> PluginHealth:
        return PluginHealth(healthy=False, detail="upstream down")


class _ExplodingPlugin(_StubPlugin):
    async def health(self) -> PluginHealth:
        raise RuntimeError("boom")


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry()


async def _register(registry: PluginRegistry, plugin: Plugin) -> Plugin:
    await plugin.initialize({})
    registry.register(plugin)
    return plugin


class TestTaskNursery:
    @pytest.mark.asyncio
    async def test_spawned_task_is_tracked(self, registry):
        await _register(registry, _StubPlugin())
        started = asyncio.Event()

        async def _work():
            started.set()
            await asyncio.sleep(60)

        task = registry.spawn_task("stub", _work())
        await started.wait()
        assert registry.get_plugin_task_count("stub") == 1
        assert not task.done()

    @pytest.mark.asyncio
    async def test_unregister_cancels_tasks(self, registry):
        await _register(registry, _StubPlugin())
        started = asyncio.Event()

        async def _work():
            started.set()
            await asyncio.sleep(60)

        task = registry.spawn_task("stub", _work())
        await started.wait()

        await registry.unregister("stub")

        assert task.cancelled() or task.done()
        assert registry.get_plugin_task_count("stub") == 0

    @pytest.mark.asyncio
    async def test_completed_task_is_reaped(self, registry):
        await _register(registry, _StubPlugin())

        async def _quick():
            return 1

        task = registry.spawn_task("stub", _quick())
        await task
        # Let the done-callback run.
        await asyncio.sleep(0)
        assert registry.get_plugin_task_count("stub") == 0

    @pytest.mark.asyncio
    async def test_failing_task_does_not_leak(self, registry):
        await _register(registry, _StubPlugin())

        async def _bad():
            raise ValueError("nope")

        task = registry.spawn_task("stub", _bad())
        with pytest.raises(ValueError):
            await task
        await asyncio.sleep(0)
        assert registry.get_plugin_task_count("stub") == 0

    @pytest.mark.asyncio
    async def test_tasks_are_isolated_per_plugin(self, registry):
        await _register(registry, _StubPlugin("a"))
        await _register(registry, _StubPlugin("b"))

        async def _work():
            await asyncio.sleep(60)

        registry.spawn_task("a", _work())
        registry.spawn_task("b", _work())
        await asyncio.sleep(0)

        await registry.cancel_plugin_tasks("a")
        assert registry.get_plugin_task_count("a") == 0
        assert registry.get_plugin_task_count("b") == 1
        await registry.cancel_plugin_tasks("b")

    @pytest.mark.asyncio
    async def test_reload_cancels_previous_tasks(self, registry):
        plugin = await _register(registry, _StubPlugin())

        async def _work():
            await asyncio.sleep(60)

        task = registry.spawn_task("stub", _work())
        await asyncio.sleep(0)

        assert await registry.reload_plugin("stub") is True
        assert task.cancelled() or task.done()
        assert plugin.is_initialized()

    @pytest.mark.asyncio
    async def test_cancel_of_unknown_plugin_is_a_noop(self, registry):
        await registry.cancel_plugin_tasks("never-existed")
        assert registry.get_plugin_task_count("never-existed") == 0


class TestBoundedCancellation:
    """A task that refuses to die costs a warning, never a frozen registry."""

    @pytest.mark.asyncio
    async def test_uncooperative_task_does_not_hang_teardown(self, registry):
        from core.plugins.nursery import PluginTaskNursery

        registry._nursery = PluginTaskNursery(cancel_timeout=0.05)
        await _register(registry, _StubPlugin())

        started = asyncio.Event()

        swallowed = 0

        async def _stubborn():
            nonlocal swallowed
            started.set()
            while True:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    # Swallows teardown's cancellation, like a plugin loop with
                    # an over-broad `except Exception`. It honours a *second*
                    # cancel only so this test can clean up its own event loop.
                    swallowed += 1
                    if swallowed > 1:
                        raise
                    continue

        task = registry.spawn_task("stub", _stubborn())
        await started.wait()

        # The whole point: teardown returns on its own deadline (0.05s) instead
        # of blocking forever on a task that will not die.
        await asyncio.wait_for(registry.unregister("stub"), timeout=2)

        assert not task.done(), "the straggler is abandoned, not awaited forever"
        assert swallowed == 1
        assert registry.get_plugin_task_count("stub") == 0

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_zero_timeout_does_not_wait(self, registry):
        from core.plugins.nursery import PluginTaskNursery

        registry._nursery = PluginTaskNursery(cancel_timeout=0)
        await _register(registry, _StubPlugin())

        async def _work():
            await asyncio.sleep(60)

        registry.spawn_task("stub", _work())
        await asyncio.sleep(0)
        assert await registry.cancel_plugin_tasks("stub") == 1

    @pytest.mark.asyncio
    async def test_spawn_is_refused_while_tearing_down(self, registry):
        """A task started mid-teardown must not outlive the cancellation."""
        from core.plugins.nursery import PluginTaskClosedError, PluginTaskNursery

        nursery = PluginTaskNursery(cancel_timeout=0.05)
        registry._nursery = nursery
        await _register(registry, _StubPlugin())

        refused: list[bool] = []

        async def _spawns_on_cancel():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                extra = asyncio.sleep(3600)
                try:
                    nursery.spawn("stub", extra)
                    refused.append(False)
                except PluginTaskClosedError:
                    refused.append(True)
                raise

        registry.spawn_task("stub", _spawns_on_cancel())
        await asyncio.sleep(0)

        await registry.cancel_plugin_tasks("stub")

        assert refused == [True]
        assert registry.get_plugin_task_count("stub") == 0

    @pytest.mark.asyncio
    async def test_spawn_works_again_after_teardown(self, registry):
        await _register(registry, _StubPlugin())
        await registry.cancel_plugin_tasks("stub")

        async def _work():
            await asyncio.sleep(60)

        task = registry.spawn_task("stub", _work())
        await asyncio.sleep(0)
        assert registry.get_plugin_task_count("stub") == 1
        task.cancel()

    @pytest.mark.asyncio
    async def test_task_names_are_never_reused(self, registry):
        """The counter is monotonic; numbering by set size repeated names."""
        await _register(registry, _StubPlugin())

        async def _quick():
            return None

        first = registry.spawn_task("stub", _quick())
        await first
        await asyncio.sleep(0)
        assert registry.get_plugin_task_count("stub") == 0

        second = registry.spawn_task("stub", _quick())
        await second
        assert first.get_name() != second.get_name()

    def test_timeout_reads_the_environment(self, monkeypatch):
        from core.plugins.nursery import (
            DEFAULT_CANCEL_TIMEOUT_SECONDS,
            get_cancel_timeout_seconds,
        )

        monkeypatch.delenv("BASELITH_PLUGIN_TASK_CANCEL_TIMEOUT", raising=False)
        assert get_cancel_timeout_seconds() == DEFAULT_CANCEL_TIMEOUT_SECONDS

        monkeypatch.setenv("BASELITH_PLUGIN_TASK_CANCEL_TIMEOUT", "2.5")
        assert get_cancel_timeout_seconds() == 2.5

        monkeypatch.setenv("BASELITH_PLUGIN_TASK_CANCEL_TIMEOUT", "not-a-number")
        assert get_cancel_timeout_seconds() == DEFAULT_CANCEL_TIMEOUT_SECONDS


class TestClosingWindow:
    """Teardown is wider than cancel_all; the window must cover all of it."""

    @pytest.mark.asyncio
    async def test_shutdown_cannot_spawn_past_the_cancellation(self, registry):
        """A plugin with no tasks that spawns from shutdown() is still refused.

        ``cancel_all`` used to return early — before opening the window — when a
        plugin owned nothing, and the window closed before ``shutdown()`` ran
        either way, so this task escaped teardown entirely.
        """
        from core.plugins.nursery import PluginTaskClosedError

        refused: list[bool] = []

        class _SpawnsOnShutdown(_StubPlugin):
            async def shutdown(self) -> None:
                leaked = asyncio.sleep(3600)
                try:
                    registry.spawn_task("stub", leaked)
                    refused.append(False)
                except PluginTaskClosedError:
                    refused.append(True)
                await super().shutdown()

        await _register(registry, _SpawnsOnShutdown())
        assert registry.get_plugin_task_count("stub") == 0  # owns nothing

        await registry.unregister("stub")

        assert refused == [True]
        assert registry.get_plugin_task_count("stub") == 0

    @pytest.mark.asyncio
    async def test_window_is_reentrant(self, registry):
        """cancel_all opens its own window inside the registry's."""
        nursery = registry._nursery
        with nursery.closing("stub"):
            assert nursery.is_closing("stub")
            await nursery.cancel_all("stub")
            assert nursery.is_closing("stub"), "inner exit re-opened the window"
        assert not nursery.is_closing("stub")

    @pytest.mark.asyncio
    async def test_window_reopens_after_teardown(self, registry):
        await _register(registry, _StubPlugin())
        await registry.unregister("stub")

        async def _work():
            await asyncio.sleep(60)

        task = registry.spawn_task("stub", _work())
        await asyncio.sleep(0)
        assert registry.get_plugin_task_count("stub") == 1
        task.cancel()

    @pytest.mark.asyncio
    async def test_reload_also_closes_the_window(self, registry):
        from core.plugins.nursery import PluginTaskClosedError

        refused: list[bool] = []

        class _SpawnsOnShutdown(_StubPlugin):
            async def shutdown(self) -> None:
                leaked = asyncio.sleep(3600)
                try:
                    registry.spawn_task("stub", leaked)
                    refused.append(False)
                except PluginTaskClosedError:
                    refused.append(True)
                await super().shutdown()

        await _register(registry, _SpawnsOnShutdown())
        assert await registry.reload_plugin("stub") is True
        assert refused == [True]


class TestHealthHook:
    def test_default_health_is_not_an_override(self):
        assert Plugin.has_health_override(_StubPlugin) is False
        assert Plugin.has_health_override(_HealthyPlugin) is True

    @pytest.mark.asyncio
    async def test_default_hook_reflects_initialization(self):
        plugin = _StubPlugin()
        assert (await plugin.health()).healthy is False
        await plugin.initialize({})
        assert (await plugin.health()).healthy is True

    @pytest.mark.asyncio
    async def test_override_surfaced_by_registry(self, registry):
        await _register(registry, _HealthyPlugin())
        report = await registry.check_health()
        assert report["healthy"] is True
        assert report["plugins"]["stub"]["detail"] == "all good"
        assert report["plugins"]["stub"]["data"] == {"queue": 0}

    @pytest.mark.asyncio
    async def test_unhealthy_override_marks_whole_report(self, registry):
        await _register(registry, _SickPlugin())
        report = await registry.check_health()
        assert report["healthy"] is False
        assert report["plugins"]["stub"]["status"] == "unhealthy"
        assert report["plugins"]["stub"]["detail"] == "upstream down"

    @pytest.mark.asyncio
    async def test_raising_hook_is_unhealthy_not_fatal(self, registry):
        await _register(registry, _ExplodingPlugin())
        report = await registry.check_health()
        assert report["healthy"] is False
        assert report["plugins"]["stub"]["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_plugin_without_override_reports_initialization(self, registry):
        await _register(registry, _StubPlugin())
        report = await registry.check_health()
        assert report["plugins"]["stub"]["status"] == "healthy"
        assert report["plugins"]["stub"]["initialized"] is True

    @pytest.mark.asyncio
    async def test_unknown_plugin_reported_not_found(self, registry):
        report = await registry.check_health("ghost")
        assert report["plugins"]["ghost"]["status"] == "not_found"
        assert report["healthy"] is False

    def test_sync_health_check_still_works(self, registry):
        report = registry.health_check()
        assert report == {"healthy": True, "plugins": {}}
