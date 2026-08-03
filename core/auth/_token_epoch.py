"""Per-user token epochs: invalidating access tokens already in circulation.

Revoking a JWT normally means blacklisting its ``jti``, which works when you
hold the token. The cases that matter most are the ones where you do not:
disabling an account, changing a password after a compromise, "sign out
everywhere". Those revoke the *refresh* token, but every access token already
minted stays valid until it expires — up to ``AUTH_SESSION_LIFETIME``. A short
TTL bounds that window; it does not close it.

An epoch closes it. Each user has a counter; every token records the value it
was minted under, and verification rejects any token whose value no longer
matches. Bumping the counter therefore invalidates that user's entire token
population at once, without enumerating a single token.

The counter lives in Redis rather than the database because it is read on the
verification path: a database round-trip per request would be both slow and a
new hard dependency for a code path that must keep working. The trade-off is
explicit — a Redis flush resets counters to zero, at which point tokens minted
under a higher epoch stop matching and are rejected. That fails *closed*
(everyone signs in again), which is the right direction for a security control.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Tokens minted before epochs existed carry no ``tv`` claim. They are accepted
# unchanged: retrofitting a rejection would sign out every active user on
# deploy, and their own expiry closes the gap within one access-token lifetime.
NO_EPOCH = 0


class TokenEpochMixin:
    """Redis-backed epoch storage, mixed into :class:`~core.auth.jwt.JWTHandler`.

    Expects the host class to provide ``_redis`` and ``_epoch_prefix``.
    """

    _redis: Any
    _epoch_prefix: str

    async def current_user_epoch(self, user_id: str) -> int:
        """The epoch a token minted for ``user_id`` right now would carry.

        Degrades to :data:`NO_EPOCH` when Redis is unreachable. That is
        deliberate on the *minting* side: refusing to issue tokens because a
        cache is down would turn a degraded dependency into a full outage,
        and the token simply ends up unprotected by this particular control
        rather than unprotected outright.
        """
        try:
            raw = await self._redis.get(self._epoch_prefix + user_id)
        except Exception as exc:  # never block token issuance
            logger.warning("token_epoch_read_failed", user_id=user_id, error=str(exc))
            return NO_EPOCH
        if raw is None:
            return NO_EPOCH
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning("token_epoch_unreadable", user_id=user_id)
            return NO_EPOCH

    async def bump_user_epoch(self, user_id: str) -> int:
        """Invalidate every access token currently held for ``user_id``.

        Call on any event that should end a user's sessions regardless of who
        is holding the tokens: password change, account disable/suspend, "sign
        out everywhere", administrative deprovisioning.

        Returns:
            The new epoch, or :data:`NO_EPOCH` if the counter could not be
            written — the caller should treat that as "the tokens are still
            live" and say so, because reporting a successful sign-out that did
            not happen is worse than reporting the failure.
        """
        try:
            new_epoch = await self._redis.incr(self._epoch_prefix + user_id)
        except Exception as exc:  # caller decides how to report the failure
            logger.error("token_epoch_bump_failed", user_id=user_id, error=str(exc))
            return NO_EPOCH
        logger.info("token_epoch_bumped", user_id=user_id, epoch=int(new_epoch))
        return int(new_epoch)

    async def epoch_is_current(self, payload: dict[str, Any]) -> bool:
        """Whether ``payload``'s epoch still matches the user's current one.

        A token with no ``tv`` claim is accepted (see :data:`NO_EPOCH`). Any
        mismatch — higher or lower — is a rejection: a token from a *higher*
        epoch than the store knows about means the store lost state, and
        honouring it would resurrect exactly the sessions a bump was meant to
        end.
        """
        claimed = payload.get("tv")
        if claimed is None:
            return True
        user_id = payload.get("sub")
        if not user_id:
            return True
        return int(claimed) == await self.current_user_epoch(str(user_id))


__all__ = ["NO_EPOCH", "TokenEpochMixin"]
