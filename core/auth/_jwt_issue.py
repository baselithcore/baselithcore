"""Token *minting*: assembling a claim set and handing it to the key ring.

Split out of ``core.auth.jwt`` to keep that module under the file-size cap.
The seam is issuance vs. verification: everything here builds a payload from
the handler's own configuration and signs it, and nothing here reads Redis,
the verify cache or the blacklist. The semantics are unchanged — ``jwt.py``
mixes this into :class:`~core.auth.jwt.JWTHandler`, so ``create_token`` and
``create_refresh_token`` remain methods on the handler exactly as before.

The recurring rule in both methods is that security-bearing claims are
*parameters*, never ``extra_claims`` entries: ``exp``, ``tenant_id``, ``act``
and ``family`` all decide something an attacker would like to decide, so they
are stripped from caller-supplied extras (see ``core.auth._jwt_claims``) and
threaded explicitly instead.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from core.auth._jwt_claims import _sanitize_extra_claims
from core.auth._jwt_keys import JWTKeyRing
from core.auth.types import AuthRole


class TokenIssuanceMixin:
    """Access- and refresh-token minting, mixed into ``JWTHandler``.

    Expects the host class to provide ``_keyring``, ``_token_lifetime``,
    ``_refresh_lifetime``, ``_issuer`` and ``_audience``.
    """

    _keyring: JWTKeyRing
    _token_lifetime: int
    _refresh_lifetime: int
    _issuer: str | None
    _audience: str | None

    def create_token(
        self,
        user_id: str,
        roles: set[AuthRole] | None = None,
        extra_claims: dict[str, Any] | None = None,
        scopes: set[str] | None = None,
        lifetime: int | None = None,
        token_epoch: int | None = None,
        tenant_id: str | None = None,
        act: dict[str, Any] | None = None,
    ) -> str:
        """
        Create an access token.

        Args:
            user_id: User identifier
            roles: User roles
            extra_claims: Additional token claims
            scopes: Explicit capability scopes to embed (``resource:action``).
                Optional; role-derived scopes are computed at check time.
            lifetime: Access-token lifetime in seconds for this token only.
                Overrides the handler default (``token_lifetime``). This is a
                first-class parameter because ``exp`` is a reserved claim and is
                stripped from ``extra_claims`` — callers needing a bounded TTL
                (e.g. impersonation) must pass it here, not via ``extra_claims``.
                Values are clamped to at least 1 second; ``None`` uses the default.
            token_epoch: The user's current token epoch, embedded as ``tv``.
                Resolved by ``AuthManager`` rather than here because reading it
                is async and this method is not. Omitted, the token carries no
                epoch and is simply not covered by bulk invalidation.
            tenant_id: Tenant the token asserts. First-class because ``tenant_id``
                is a reserved claim (the isolation boundary) and is stripped from
                ``extra_claims`` — callers must pass it here, not via extras.
            act: RFC 8693 actor claim for a delegated/impersonation token.
                First-class because ``act`` is reserved (it decides
                re-delegation refusal and capability-only adjudication) and is
                stripped from ``extra_claims``.

        Returns:
            Encoded token string
        """
        now = int(time.time())
        token_id = secrets.token_hex(8)

        effective_lifetime = (
            self._token_lifetime if lifetime is None else max(1, int(lifetime))
        )
        payload: dict[str, Any] = {
            "sub": user_id,
            "iat": now,
            "exp": now + effective_lifetime,
            "jti": token_id,
            "roles": [r.value for r in (roles or {AuthRole.USER})],
        }
        if scopes:
            payload["scopes"] = sorted(scopes)
        if token_epoch is not None:
            # Stamped even at 0. Skipping the claim for the initial epoch
            # would leave every token minted before a user's *first* bump
            # indistinguishable from a legacy token, and therefore immune
            # to that bump — the one that usually matters most.
            payload["tv"] = token_epoch
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if act:
            payload["act"] = act
        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience
        safe_extra = _sanitize_extra_claims(extra_claims)
        if safe_extra:
            payload.update(safe_extra)

        return self._keyring.encode(payload)

    def create_refresh_token(
        self,
        user_id: str,
        roles: set[AuthRole] | None = None,
        tenant_id: str | None = None,
        extra_claims: dict[str, Any] | None = None,
        *,
        family: str | None = None,
    ) -> str:
        """Create a refresh token, optionally preserving auth context.

        Args:
            user_id: User identifier.
            roles: Roles to preserve across rotation.
            tenant_id: Tenant to preserve across rotation.
            extra_claims: Additional claims (reserved keys are dropped).
            family: Rotation-lineage id. Internal — ``rotate_refresh_token``
                threads the consumed token's family through so reuse of ANY
                ancestor revokes the whole lineage. A fresh login leaves it
                ``None`` and the new token starts its own family (= its jti).
        """
        now = int(time.time())
        token_id = secrets.token_hex(8)
        payload: dict[str, Any] = {
            "sub": user_id,
            "iat": now,
            "exp": now + self._refresh_lifetime,
            "jti": token_id,
            "type": "refresh",
            "family": family or token_id,
        }
        if roles:
            payload["roles"] = [r.value for r in roles]
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if self._issuer:
            payload["iss"] = self._issuer
        if self._audience:
            payload["aud"] = self._audience
        safe_extra = _sanitize_extra_claims(extra_claims)
        if safe_extra:
            payload.update(safe_extra)
        return self._keyring.encode(payload)


__all__ = ["TokenIssuanceMixin"]
