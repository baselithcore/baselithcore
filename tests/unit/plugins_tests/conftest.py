"""Shared test env for baselithbot plugin tests.

The plugin now fails closed on dashboard write endpoints and inbound
webhooks when their auth secrets are not configured. Opt into the
documented insecure dev mode here so existing unit tests continue to
exercise write paths without each test supplying a bearer token or
signature header.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BASELITHBOT_DASHBOARD_ALLOW_INSECURE", "1")
os.environ.setdefault("BASELITHBOT_INBOUND_INSECURE", "1")


@pytest.fixture(autouse=True)
def _reset_shared_http_pool():
    """Isolate the module-global shared httpx pool between tests.

    Channel adapters and the ClawHub client now reuse a process-wide pooled
    client; without resetting it, a fake client patched into one test would be
    cached and served to the next. Clear the registry around every test.
    """
    try:
        from plugins.baselithbot.browser import http_pool
    except Exception:
        yield
        return
    http_pool._GLOBAL_POOL._clients.clear()
    yield
    http_pool._GLOBAL_POOL._clients.clear()
