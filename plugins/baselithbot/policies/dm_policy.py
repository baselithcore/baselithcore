"""DM-pairing + sender allowlist policy engine for inbound channel traffic."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .rate_limit import RateLimiter


class PolicyDenied(PermissionError):
    """Raised when a channel event violates a configured policy."""


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class _ChannelPolicy(BaseModel):
    dm_only: bool = False
    allowed_senders: list[str] = Field(default_factory=list)
    blocked_senders: list[str] = Field(default_factory=list)
    rate_limit_window_s: float = 60.0
    rate_limit_max_events: int = 30


class DMPairingPolicy:
    """Per-channel policy table + sliding-window rate limiter."""

    def __init__(self) -> None:
        self._policies: dict[str, _ChannelPolicy] = {}
        self._limiters: dict[str, RateLimiter] = {}

    def configure(
        self,
        channel: str,
        *,
        dm_only: bool = False,
        allowed_senders: list[str] | None = None,
        blocked_senders: list[str] | None = None,
        rate_limit_window_s: float = 60.0,
        rate_limit_max_events: int = 30,
    ) -> None:
        self._policies[channel] = _ChannelPolicy(
            dm_only=dm_only,
            allowed_senders=allowed_senders or [],
            blocked_senders=blocked_senders or [],
            rate_limit_window_s=rate_limit_window_s,
            rate_limit_max_events=rate_limit_max_events,
        )
        self._limiters[channel] = RateLimiter(
            window_seconds=rate_limit_window_s,
            max_events=rate_limit_max_events,
        )

    def evaluate(
        self,
        channel: str,
        sender: str | None,
        is_dm: bool = False,
    ) -> PolicyDecision:
        policy = self._policies.get(channel)
        if policy is None:
            return PolicyDecision(allowed=True, reason="no policy configured")

        if policy.dm_only and not is_dm:
            return PolicyDecision(allowed=False, reason="dm_only policy violated")
        if sender and sender in policy.blocked_senders:
            return PolicyDecision(allowed=False, reason=f"sender '{sender}' is blocked")
        if policy.allowed_senders and (
            sender is None or sender not in policy.allowed_senders
        ):
            return PolicyDecision(
                allowed=False, reason=f"sender '{sender}' not in allowlist"
            )

        limiter = self._limiters.get(channel)
        if limiter is not None:
            key = f"{channel}:{sender or '*'}"
            if not limiter.consume(key):
                return PolicyDecision(
                    allowed=False,
                    reason="rate limit exceeded",
                    metadata={"remaining": 0},
                )

        return PolicyDecision(allowed=True, reason="all checks passed")

    def require(self, channel: str, sender: str | None, is_dm: bool = False) -> None:
        decision = self.evaluate(channel, sender, is_dm)
        if not decision.allowed:
            raise PolicyDenied(decision.reason)

    def status(self) -> dict[str, Any]:
        return {
            "policies": {k: v.model_dump() for k, v in self._policies.items()},
            "rate_limiters": {
                k: limiter.status() for k, limiter in self._limiters.items()
            },
        }


__all__ = ["DMPairingPolicy", "PolicyDecision", "PolicyDenied"]
