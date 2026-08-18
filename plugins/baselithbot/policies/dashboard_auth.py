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
- Query-parameter (``?token=...``) fallback is **never accepted** — the
  token would leak into access logs, browser history, and Referer headers.
  The SSE live-event stream (browser ``EventSource`` cannot send headers)
  uses **single-use stream tickets** instead: an authenticated client mints
  one via ``POST /dash/events/ticket`` (bearer-gated) and connects with
  ``?ticket=``. A ticket is random, bound to no other capability, expires
  in ~30 seconds, and is consumed on first use — so the value that does
  land in URLs and logs is worthless by the time anyone reads it.
- **Every** dashboard endpoint is gated — reads included. Read routes
  return decrypted desktop/browser screenshots, full session transcripts,
  and the audit log, which are exactly as sensitive as the writes.
- Constant-time token comparison (``hmac.compare_digest``) avoids
  timing side-channels on token validation.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time

from core.observability.logging import get_logger
from fastapi import HTTPException, Request, status

logger = get_logger(__name__)

#: Stream tickets expire this many seconds after minting.
STREAM_TICKET_TTL_SECONDS = 30.0

_ENV_TOKEN = "BASELITHBOT_DASHBOARD_TOKEN"  # noqa: S105 - env var NAME, not a hardcoded secret
_ENV_INSECURE = "BASELITHBOT_DASHBOARD_ALLOW_INSECURE"


def _insecure_bypass_enabled() -> bool:
    return os.environ.get(_ENV_INSECURE, "").strip().lower() in {"1", "true", "yes"}


class DashboardAuth:
    """Bearer-token guard for dashboard write endpoints (fail-closed)."""

    def __init__(
        self,
        token: str | None = None,
        *,
        allow_insecure: bool | None = None,
    ) -> None:
        self._token = token or os.environ.get(_ENV_TOKEN, "").strip() or None
        self._allow_insecure = (
            allow_insecure if allow_insecure is not None else _insecure_bypass_enabled()
        )
        self._warned = False
        # Single-use SSE stream tickets: ticket -> expiry (monotonic seconds).
        self._stream_tickets: dict[str, float] = {}

    def mint_stream_ticket(self) -> str:
        """Mint a short-lived single-use ticket for the SSE stream.

        Callers must already be authenticated (the minting endpoint sits
        behind the bearer guard). The ticket exists because ``EventSource``
        cannot send headers: it grants exactly one stream connection within
        :data:`STREAM_TICKET_TTL_SECONDS`, so its exposure in URLs and access
        logs is worthless after use/expiry — unlike the long-lived token.
        """
        now = time.monotonic()
        # Lazy prune keeps the dict bounded by the mint rate x TTL.
        self._stream_tickets = {t: exp for t, exp in self._stream_tickets.items() if exp > now}
        ticket = secrets.token_urlsafe(32)
        self._stream_tickets[ticket] = now + STREAM_TICKET_TTL_SECONDS
        return ticket

    def _consume_stream_ticket(self, ticket: str) -> bool:
        """Validate and burn a stream ticket (single use)."""
        expiry = self._stream_tickets.pop(ticket, None)
        return expiry is not None and expiry > time.monotonic()

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
            # SSE requests cannot carry headers from EventSource; they may
            # instead present a single-use stream ticket minted by an already
            # authenticated call to POST /dash/events/ticket. Never the raw
            # token: query strings land in access logs and browser history.
            accept = request.headers.get("accept", "")
            if accept.lower().startswith("text/event-stream"):
                ticket = (request.query_params.get("ticket") or "").strip()
                if ticket and self._consume_stream_ticket(ticket):
                    return
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


def _extract_token(request: Request) -> str | None:
    """Return the bearer token from ``Authorization`` — header only."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        value = header.split(" ", 1)[1].strip()
        if value:
            return value
    return None


__all__ = ["DashboardAuth"]
