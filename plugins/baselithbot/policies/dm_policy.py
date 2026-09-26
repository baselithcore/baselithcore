"""DM-pairing + sender allowlist policy engine for inbound channel traffic."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.policies.rate_limit import RateLimiter
from pydantic import BaseModel, Field


class PolicyDenied(PermissionError):  # noqa: N818 - public API; rename would break dependents
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

    def configure_from_mapping(self, mapping: Any) -> int:
        """Apply the ``baselithbot.dm_policy`` section of ``plugins.yaml``.

        ``mapping`` is ``{channel: {allowed_senders, blocked_senders, dm_only,
        rate_limit_window_s, rate_limit_max_events}}`` — the shape written by
        ``baselith baselithbot pairing approve``. Without this call the
        documented allowlist was persisted but never enforced: ``evaluate``
        answered "no policy configured" and every sender got through.

        Returns:
            Number of channels configured.

        Raises:
            ValueError: On a malformed section — fail loudly at startup rather
                than silently run without the allowlist the operator wrote.
        """
        if mapping is None:
            return 0
        if not isinstance(mapping, dict):
            raise ValueError("baselithbot.dm_policy must be a mapping of channel -> policy")
        for channel, raw in mapping.items():
            if raw is not None and not isinstance(raw, dict):
                raise ValueError(f"baselithbot.dm_policy.{channel} must be a mapping")
            data = dict(raw or {})
            # YAML reads numeric ids (Telegram, Discord) as ints; senders are
            # compared as strings, so normalise before validation.
            for key in ("allowed_senders", "blocked_senders"):
                if isinstance(data.get(key), list):
                    data[key] = [str(s) for s in data[key]]
            policy = _ChannelPolicy.model_validate(data)
            self.configure(
                str(channel).strip().lower(),
                dm_only=policy.dm_only,
                allowed_senders=policy.allowed_senders,
                blocked_senders=policy.blocked_senders,
                rate_limit_window_s=policy.rate_limit_window_s,
                rate_limit_max_events=policy.rate_limit_max_events,
            )
        return len(mapping)

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
        if policy.allowed_senders and (sender is None or sender not in policy.allowed_senders):
            return PolicyDecision(allowed=False, reason=f"sender '{sender}' not in allowlist")

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
            "rate_limiters": {k: limiter.status() for k, limiter in self._limiters.items()},
        }


__all__ = ["DMPairingPolicy", "PolicyDecision", "PolicyDenied"]
