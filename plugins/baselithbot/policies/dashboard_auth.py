"""Dashboard bearer-token auth with dev-mode fallback.

The Baselithbot dashboard exposes sensitive write endpoints (issue pairing
tokens, delete sessions, remove cron jobs, launch agent tasks). To avoid
turning every self-hosted deploy into an open control plane, we gate these
actions behind a shared-secret bearer token when one is configured.

Security model
--------------
- ``BASELITHBOT_DASHBOARD_TOKEN`` env var (or ``token`` constructor arg)
  holds the secret. When set, all gated endpoints require
  ``Authorization: Bearer <token>`` OR a ``?token=<token>`` query param.
- When unset, the dashboard behaves as open (dev mode) and logs a single
  warning on the first protected call.
- Read-only GET endpoints stay open by default; only the explicitly listed
  sensitive verbs call into ``require_dashboard_auth``.
- Constant-time token comparison (``hmac.compare_digest``) avoids timing
  side-channels on token validation.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import HTTPException, Request, status

from core.observability.logging import get_logger

logger = get_logger(__name__)

_ENV_VAR = "BASELITHBOT_DASHBOARD_TOKEN"


class DashboardAuth:
    """Optional bearer-token guard for dashboard write endpoints."""

    def __init__(self, token: Optional[str] = None) -> None:
        self._token = token or os.environ.get(_ENV_VAR, "").strip() or None
        self._warned = False

    @property
    def enabled(self) -> bool:
        return self._token is not None

    def check(self, request: Request) -> None:
        """Raise 401/403 when the request is missing or presents a bad token.

        No-op when the server has no configured token (open dev mode); the
        first unauthenticated call emits a single warning line.
        """
        if self._token is None:
            if not self._warned:
                logger.warning(
                    "baselithbot_dashboard_open",
                    reason=(
                        f"{_ENV_VAR} is not set; write endpoints are unguarded"
                    ),
                )
                self._warned = True
            return

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
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        value = header.split(" ", 1)[1].strip()
        if value:
            return value
    qs_token = request.query_params.get("token", "").strip()
    return qs_token or None


__all__ = ["DashboardAuth"]
