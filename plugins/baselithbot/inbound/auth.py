"""Inbound webhook authentication per channel.

Provides a single ``verify_inbound_request`` entrypoint called by the
router before parsing or dispatching any event. The verifier is
**fail-closed**: a missing secret or a missing signature header both
reject the request with 401 so forged Slack/Telegram/Discord events
cannot reach the dispatcher.

Operators can opt-in to an open mode for local development by setting
``BASELITHBOT_INBOUND_INSECURE=1``. The router logs a single warning on
the first bypass so the open state is visible.

Required environment variables (only for the channels you expose):

- ``SLACK_SIGNING_SECRET``
- ``TELEGRAM_WEBHOOK_SECRET``
- ``DISCORD_PUBLIC_KEY`` (hex, from the Discord application)
- ``BASELITHBOT_INBOUND_GENERIC_SECRET`` (optional HMAC-SHA256 over body
  for the ``generic`` channel; expected in ``X-Baselithbot-Signature``)
"""

from __future__ import annotations

import os
import time
from typing import Any

from core.observability.logging import get_logger
from plugins.baselithbot.inbound.signatures import (
    verify_discord_signature,
    verify_slack_signature,
    verify_telegram_secret_token,
)
from plugins.baselithbot.inbound.verify import verify_hmac_signature

logger = get_logger(__name__)

_ENV_INSECURE = "BASELITHBOT_INBOUND_INSECURE"
_SLACK_TIMESTAMP_SKEW_SECONDS = 5 * 60  # mirrors Slack's recommendation

_warned_channels: set[str] = set()


class InboundAuthError(RuntimeError):
    """Raised when an inbound request fails signature verification."""

    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


def _insecure_bypass_enabled() -> bool:
    return os.environ.get(_ENV_INSECURE, "").strip().lower() in {"1", "true", "yes"}


def _warn_once(channel: str, reason: str) -> None:
    if channel in _warned_channels:
        return
    _warned_channels.add(channel)
    logger.warning(
        "baselithbot_inbound_insecure",
        channel=channel,
        reason=reason,
    )


def _missing_secret(channel: str, env_var: str) -> None:
    if _insecure_bypass_enabled():
        _warn_once(
            channel,
            f"{env_var} not set and {_ENV_INSECURE}=1; accepting unsigned requests",
        )
        return
    raise InboundAuthError(
        status_code=503,
        reason=(
            f"{env_var} is not configured; refuse to accept {channel} events. "
            f"Set the env var or {_ENV_INSECURE}=1 for local development."
        ),
    )


def _unauthorized(reason: str) -> InboundAuthError:
    return InboundAuthError(status_code=401, reason=reason)


def _verify_slack(headers: dict[str, str], body: bytes) -> None:
    secret = os.environ.get("SLACK_SIGNING_SECRET", "").strip()
    if not secret:
        _missing_secret("slack", "SLACK_SIGNING_SECRET")
        return
    timestamp = headers.get("x-slack-request-timestamp", "").strip()
    signature = headers.get("x-slack-signature", "").strip()
    if not timestamp or not signature:
        raise _unauthorized("missing Slack signature headers")
    try:
        ts_int = int(timestamp)
    except ValueError as exc:
        raise _unauthorized("invalid Slack timestamp") from exc
    if abs(time.time() - ts_int) > _SLACK_TIMESTAMP_SKEW_SECONDS:
        raise _unauthorized("Slack timestamp outside skew window")
    if not verify_slack_signature(secret, timestamp, body, signature):
        raise _unauthorized("invalid Slack signature")


def _verify_telegram(headers: dict[str, str]) -> None:
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not secret:
        _missing_secret("telegram", "TELEGRAM_WEBHOOK_SECRET")
        return
    received = headers.get("x-telegram-bot-api-secret-token", "")
    if not verify_telegram_secret_token(secret, received):
        raise _unauthorized("invalid Telegram secret token")


def _verify_discord(headers: dict[str, str], body: bytes) -> None:
    public_key = os.environ.get("DISCORD_PUBLIC_KEY", "").strip()
    if not public_key:
        _missing_secret("discord", "DISCORD_PUBLIC_KEY")
        return
    signature = headers.get("x-signature-ed25519", "").strip()
    timestamp = headers.get("x-signature-timestamp", "").strip()
    if not signature or not timestamp:
        raise _unauthorized("missing Discord signature headers")
    if not verify_discord_signature(public_key, timestamp, body, signature):
        raise _unauthorized("invalid Discord signature")


def _verify_generic(channel: str, headers: dict[str, str], body: bytes) -> None:
    secret = os.environ.get("BASELITHBOT_INBOUND_GENERIC_SECRET", "").strip()
    if not secret:
        _missing_secret(channel, "BASELITHBOT_INBOUND_GENERIC_SECRET")
        return
    signature = headers.get("x-baselithbot-signature", "").strip()
    if not signature:
        raise _unauthorized("missing X-Baselithbot-Signature header")
    if not verify_hmac_signature(secret, body, signature):
        raise _unauthorized("invalid X-Baselithbot-Signature")


def verify_inbound_request(
    channel: str,
    headers: dict[str, str],
    body: bytes,
) -> None:
    """Verify an inbound webhook. Raise :class:`InboundAuthError` on failure.

    ``headers`` must be provided with lowercased keys.
    """
    lowered: dict[str, Any] = {k.lower(): v for k, v in headers.items()}
    normalized = channel.strip().lower()
    if normalized == "slack":
        _verify_slack(lowered, body)
        return
    if normalized == "telegram":
        _verify_telegram(lowered)
        return
    if normalized == "discord":
        _verify_discord(lowered, body)
        return
    _verify_generic(normalized, lowered, body)


__all__ = ["InboundAuthError", "verify_inbound_request"]
