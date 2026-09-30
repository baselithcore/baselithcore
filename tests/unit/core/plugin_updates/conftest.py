"""Hermetic defaults for the plugin_updates tests.

``build_announcement_gate`` picks Redis whenever the environment carries
``CACHE_BACKEND=redis`` plus a URL (a developer ``.env``), which would make the
tests claim keys in a real Redis. Force the file-lock gate for every test; a
test that wants a Redis-backed gate patches ``core.config.get_storage_config``
itself (later patches win) and injects a fake client.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _announcement_gate_uses_file_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.config as config_pkg

    real = config_pkg.get_storage_config

    def _without_redis() -> object:
        # Keep every other storage field; only drop the shared Redis.
        return real().model_copy(
            update={"cache_backend": "memory", "cache_redis_url": ""}
        )

    monkeypatch.setattr(config_pkg, "get_storage_config", _without_redis)
