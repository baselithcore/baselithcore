"""Dashboard bearer-token auth. Fail-closed by default.

The Baselithbot dashboard exposes sensitive write endpoints (issue pairing
tokens, delete sessions, remove cron jobs, launch agent tasks). These
actions are gated behind a shared-secret bearer token.

Security model
--------------
- ``BASELITHBOT_DASHBOARD_TOKEN`` env var (or ``token`` constructor arg)
  holds the secret. When set, all gated endpoints require the token in
  the ``Authorization: Bearer <token>`` HTTP header.
- When the env var is NOT set, the dashboard refuses gated calls with
  503. Operators can opt-in to an open mode for local development via
  ``BASELITHBOT_DASHBOARD_ALLOW_INSECURE=1``; this logs a loud warning
  on every gated call until configured.
- Query-parameter (``?token=...``) fallback is **not accepted** — the
  token leaks into access logs, browser history, and Referer headers.
- Read-only GET endpoints stay open; only the explicitly listed
  sensitive verbs call into ``require_dashboard_auth``.
- Constant-time token comparison (``hmac.compare_digest``) avoids
  timing side-channels on token validation.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import HTTPException, Request, status

from core.observability.logging import get_logger

logger = get_logger(__name__)

_ENV_TOKEN = "BASELITHBOT_DASHBOARD_TOKEN"
_ENV_INSECURE = "BASELITHBOT_DASHBOARD_ALLOW_INSECURE"


def _insecure_bypass_enabled() -> bool:
    return os.environ.get(_ENV_INSECURE, "").strip().lower() in {"1", "true", "yes"}


class DashboardAuth:
    """Bearer-token guard for dashboard write endpoints (fail-closed)."""

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        allow_insecure: Optional[bool] = None,
    ) -> None:
        self._token = token or os.environ.get(_ENV_TOKEN, "").strip() or None
        self._allow_insecure = (
            allow_insecure if allow_insecure is not None else _insecure_bypass_enabled()
        )
        self._warned = False

    @property
    def enabled(self) -> bool:
        return self._token is not None

    def check(self, request: Request) -> None:
        """Raise on any gated request that is not authenticated.

        - Token configured: require a matching ``Authorization: Bearer``
          header (401 missing / 403 mismatch).
        - Token missing + insecure flag: log a warning, allow.
        - Token missing + no insecure flag: refuse with 503.
        """
        if self._token is None:
            if self._allow_insecure:
                if not self._warned:
                    logger.warning(
                        "baselithbot_dashboard_open",
                        reason=(
                            f"{_ENV_TOKEN} is not set and {_ENV_INSECURE}=1; "
                            "write endpoints are unguarded (dev mode)"
                        ),
                    )
                    self._warned = True
                return
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"{_ENV_TOKEN} is not configured; refusing dashboard write. "
                    f"Set the env var or {_ENV_INSECURE}=1 for local development."
                ),
            )

        presented = _extract_token(request)
        if presented is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing dashboard bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not hmac.compare_digest(presented, self._token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid dashboard bearer token",
            )


def _extract_token(request: Request) -> Optional[str]:
    """Return the bearer token from ``Authorization`` — header only."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        value = header.split(" ", 1)[1].strip()
        if value:
            return value
    return None


__all__ = ["DashboardAuth"]
