"""SSRF-hardened HTTP client shared by every baselithbot outbound call.

Channel webhooks, integrations, and skills all take their target URL from
user configuration, which makes them SSRF vectors. Route every outbound
request through :func:`hardened_client`; set
``BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS=true`` only for trusted local setups
(e.g. a Matrix homeserver on the LAN).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from core.security.http import create_hardened_async_client
from core.security.ssrf import SsrfPolicy


def _allow_internal() -> bool:
    raw = os.environ.get("BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def hardened_client(**kwargs: Any) -> httpx.AsyncClient:
    """Build the SSRF-guarded ``httpx.AsyncClient`` for outbound calls."""
    return create_hardened_async_client(
        policy=SsrfPolicy(allow_internal=_allow_internal()), **kwargs
    )


__all__ = ["hardened_client"]
