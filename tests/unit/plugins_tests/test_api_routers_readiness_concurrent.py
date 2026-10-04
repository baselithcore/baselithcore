"""``/health/ready`` probes its dependencies concurrently: a cache miss costs
the slowest probe, not the sum of the three timeouts."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from fastapi import Response

from plugins.api_routers import status as status_mod


async def test_readiness_probes_run_concurrently() -> None:
    started = 0
    all_started = asyncio.Event()

    def _probe(name: str):
        async def run() -> bool:
            nonlocal started
            started += 1
            if started == 3:
                all_started.set()
            # Sequential awaits would deadlock here: each probe waits for the
            # other two to have *started*.
            await asyncio.wait_for(all_started.wait(), timeout=1.0)
            return True

        return run

    status_mod.reset_readiness_cache()
    with (
        patch.object(status_mod, "_check_database", _probe("db")),
        patch.object(status_mod, "_check_redis", _probe("redis")),
        patch.object(status_mod, "_check_vectorstore", _probe("vec")),
    ):
        body = await status_mod.readiness(Response())
    assert body["status"] == "ready"
    assert body["services"] == {"database": True, "redis": True, "vectorstore": True}
