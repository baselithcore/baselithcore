"""Unit tests for the Baselithbot plugin — cron scheduler and custom cron registry."""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_cron_scheduler_runs_job() -> None:
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    sched.add_interval("tick", tick, seconds=1)
    sched._jobs["tick"].next_run_at = 0.0
    await sched.start()
    await asyncio.sleep(1.5)
    await sched.stop()
    assert counter["n"] >= 1


@pytest.mark.asyncio
async def test_cron_scheduler_pause_resume_blocks_execution() -> None:
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    sched.add_interval("tick", tick, seconds=1, description="unit test")
    sched._jobs["tick"].next_run_at = 0.0
    assert sched.set_enabled("tick", False) is True
    await sched.start()
    await asyncio.sleep(0.5)
    assert counter["n"] == 0
    assert sched.set_enabled("tick", True) is True
    sched._jobs["tick"].next_run_at = 0.0
    await asyncio.sleep(0.7)
    await sched.stop()
    assert counter["n"] >= 1


@pytest.mark.asyncio
async def test_cron_scheduler_trigger_runs_immediately() -> None:
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    sched.add_interval("tick", tick, seconds=3600)
    await sched.start()
    await asyncio.sleep(0.05)
    assert counter["n"] == 0
    assert sched.trigger("tick") is True
    await asyncio.sleep(0.5)
    await sched.stop()
    assert counter["n"] >= 1


@pytest.mark.asyncio
async def test_cron_scheduler_set_interval_updates_schedule() -> None:
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()

    async def noop():
        return None

    sched.add_interval("job", noop, seconds=10)
    assert sched.set_interval("job", 2) is True
    info = sched.get("job")
    assert info is not None
    assert info["interval_seconds"] == 2
    assert sched.set_interval("missing", 5) is False
    with pytest.raises(ValueError):
        sched.set_interval("job", 0)


def test_cron_scheduler_get_returns_description_and_none_for_missing() -> None:
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()

    async def noop():
        return None

    sched.add_interval("job", noop, seconds=5, description="hello")
    info = sched.get("job")
    assert info is not None
    assert info["description"] == "hello"
    assert info["enabled"] is True
    assert sched.get("missing") is None


def test_custom_cron_store_persists_specs(tmp_path) -> None:
    from plugins.baselithbot.cron.custom import (
        CronActionSpec,
        CustomCronSpec,
        CustomCronStore,
    )

    store = CustomCronStore(tmp_path / "custom_crons.json")
    assert store.load() == []
    specs = [
        CustomCronSpec(
            name="custom.ping",
            interval_seconds=30,
            action=CronActionSpec(type="log", params={"message": "hi"}),
        )
    ]
    store.save(specs)
    reloaded = store.load()
    assert len(reloaded) == 1
    assert reloaded[0].name == "custom.ping"
    assert reloaded[0].action.type == "log"


def test_custom_cron_registry_register_auto_prefixes_and_persists(tmp_path) -> None:
    from plugins.baselithbot.cron.custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    store = CustomCronStore(tmp_path / "custom.json")
    registry = CustomCronRegistry(scheduler=sched, store=store)

    spec = CustomCronSpec(
        name="ping",  # no prefix -> registry should add it
        interval_seconds=60,
        action=CronActionSpec(type="log", params={"message": "ok"}),
    )
    stored = registry.register(spec)
    assert stored.name == "custom.ping"
    assert sched.get("custom.ping") is not None

    # Same name collides.
    with pytest.raises(ValueError):
        registry.register(spec)

    # Persistence round-trip.
    registry2 = CustomCronRegistry(scheduler=CronScheduler(), store=store)
    loaded = registry2.bootstrap()
    assert loaded == 1
    assert registry2.get("custom.ping") is not None


def test_custom_cron_registry_rejects_unknown_action(tmp_path) -> None:
    from plugins.baselithbot.cron.custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )
    from plugins.baselithbot.cron.scheduler import CronScheduler

    registry = CustomCronRegistry(
        scheduler=CronScheduler(),
        store=CustomCronStore(tmp_path / "custom.json"),
    )
    spec = CustomCronSpec(
        name="x",
        interval_seconds=30,
        action=CronActionSpec(type="nuclear_launch", params={}),
    )
    with pytest.raises(ValueError):
        registry.register(spec)


def test_custom_cron_registry_chat_command_requires_slash(tmp_path) -> None:
    from plugins.baselithbot.cron.custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )
    from plugins.baselithbot.cron.scheduler import CronScheduler

    registry = CustomCronRegistry(
        scheduler=CronScheduler(),
        store=CustomCronStore(tmp_path / "custom.json"),
    )
    bad = CustomCronSpec(
        name="cc",
        interval_seconds=30,
        action=CronActionSpec(type="chat_command", params={"command": "status"}),
    )
    with pytest.raises(ValueError):
        registry.register(bad)


@pytest.mark.asyncio
async def test_custom_cron_log_action_executes(tmp_path) -> None:
    from plugins.baselithbot.cron.custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    registry = CustomCronRegistry(
        scheduler=sched,
        store=CustomCronStore(tmp_path / "custom.json"),
    )
    registry.register(
        CustomCronSpec(
            name="ping",
            interval_seconds=1,
            action=CronActionSpec(
                type="log",
                params={"message": "tick", "level": "info"},
            ),
        )
    )
    sched._jobs["custom.ping"].next_run_at = 0.0
    await sched.start()
    await asyncio.sleep(0.3)
    info = sched.get("custom.ping")
    await sched.stop()
    assert info is not None
    assert int(info["runs"]) >= 1  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_custom_cron_chat_command_dispatches(tmp_path) -> None:
    from plugins.baselithbot.chat.commands import ChatCommandRouter
    from plugins.baselithbot.cron.custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )
    from plugins.baselithbot.cron.scheduler import CronScheduler

    router = ChatCommandRouter()
    sched = CronScheduler()
    registry = CustomCronRegistry(
        scheduler=sched,
        store=CustomCronStore(tmp_path / "custom.json"),
        chat_commands=router,
    )
    registry.register(
        CustomCronSpec(
            name="status-heartbeat",
            interval_seconds=1,
            action=CronActionSpec(
                type="chat_command",
                params={"command": "/status"},
            ),
        )
    )
    sched._jobs["custom.status-heartbeat"].next_run_at = 0.0
    await sched.start()
    await asyncio.sleep(0.3)
    await sched.stop()
    assert router._stats["status"] >= 1


def test_custom_cron_registry_update_and_delete(tmp_path) -> None:
    from plugins.baselithbot.cron.custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    registry = CustomCronRegistry(
        scheduler=sched,
        store=CustomCronStore(tmp_path / "custom.json"),
    )
    registry.register(
        CustomCronSpec(
            name="job",
            interval_seconds=30,
            action=CronActionSpec(type="log", params={"message": "a"}),
        )
    )
    registry.update(
        "custom.job",
        CustomCronSpec(
            name="custom.job",
            interval_seconds=45,
            action=CronActionSpec(type="log", params={"message": "b"}),
            enabled=False,
        ),
    )
    info = sched.get("custom.job")
    assert info is not None
    assert info["interval_seconds"] == 45
    assert info["enabled"] is False
    assert registry.delete("custom.job") is True
    assert sched.get("custom.job") is None
    assert registry.delete("custom.job") is False


def test_cron_scheduler_backend_label() -> None:
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    assert sched.backend == "interval"


def test_cron_sleep_until_next_returns_min_interval() -> None:
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()

    async def noop():
        return None

    sched.add_interval("a", noop, seconds=2)
    sched.add_interval("b", noop, seconds=10)
    sleep_for = sched._sleep_until_next(now=__import__("time").time())
    assert 0 < sleep_for <= 2.0
