"""List-changed announcements are referenced until done and log failures."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from core.mcp import server as server_mod
from core.mcp.server import MCPServer


def _server_with_subscribers() -> MCPServer:
    srv = MCPServer(name="t", version="0")
    patcher = patch.object(
        type(srv._subscriptions),
        "active",
        new_callable=lambda: property(lambda _s: True),
    )
    patcher.start()
    srv._patcher = patcher  # type: ignore[attr-defined]
    return srv


async def test_announce_keeps_a_reference_until_the_task_completes() -> None:
    srv = _server_with_subscribers()
    try:
        gate = asyncio.Event()

        async def notifier() -> None:
            await gate.wait()

        srv._announce(notifier)
        assert len(srv._announce_tasks) == 1
        gate.set()
        await asyncio.gather(*srv._announce_tasks)
        await asyncio.sleep(0)
        assert not srv._announce_tasks
    finally:
        srv._patcher.stop()  # type: ignore[attr-defined]


async def test_a_failed_announce_is_logged() -> None:
    srv = _server_with_subscribers()
    try:

        async def notifier() -> None:
            raise RuntimeError("transport gone")

        with patch.object(server_mod, "logger") as fake_logger:
            srv._announce(notifier)
            task = next(iter(srv._announce_tasks))
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        fake_logger.warning.assert_called_once()
        assert fake_logger.warning.call_args.args[0] == (
            "mcp_list_changed_announce_failed"
        )
        assert not srv._announce_tasks
    finally:
        srv._patcher.stop()  # type: ignore[attr-defined]


def test_announce_without_a_loop_is_a_noop() -> None:
    srv = _server_with_subscribers()
    try:
        calls: list[int] = []

        async def notifier() -> None:  # pragma: no cover - never scheduled
            calls.append(1)

        srv._announce(notifier)
        assert not srv._announce_tasks
        assert not calls
    finally:
        srv._patcher.stop()  # type: ignore[attr-defined]
