"""
Federated SSO via OpenID Connect (OIDC) bearer-token verification.

Lets BaselithCore accept access/ID tokens minted by an external identity
provider (Okta, Auth0, Azure AD, Keycloak, …) instead of — or alongside — its
own HS256 tokens. The provider's public keys are fetched from its JWKS endpoint
(discovered from ``{issuer}/.well-known/openid-configuration`` unless an explicit
URL is configured) and the token's RS256/ES256 signature, ``iss`` and ``aud``
are validated with PyJWT.

The feature is **opt-in** (``OIDC_ENABLED``) and **additive**: when disabled the
verifier reports ``is_configured == False`` and :class:`AuthManager` never calls
it, so local JWT / API-key authentication is unchanged.

Claim → identity mapping is configurable per IdP:

* ``OIDC_USERNAME_CLAIM`` (default ``sub``) → ``AuthUser.user_id``
* ``OIDC_ROLES_CLAIM`` (default ``roles``) → roles, translated through
  ``OIDC_ROLE_MAP`` to :class:`~core.auth.types.AuthRole` values
* ``OIDC_SCOPES_CLAIM`` (default ``scope``) → explicit capability scopes
  (OAuth's space-delimited ``scope`` string and JSON arrays both supported)
* ``OIDC_TENANT_CLAIM`` (optional) → ``AuthUser.tenant_id``
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import jwt
from jwt import PyJWKClient

from core.auth.types import AuthRole, AuthUser, InvalidTokenError
from core.config.security import SecurityConfig, get_security_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

# Discovery document suffix per the OIDC spec.
_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
# Bound the discovery/JWKS HTTP calls so a slow IdP cannot hang a request.
_DISCOVERY_TIMEOUT_S = 5.0


class OIDCVerifier:
    """Verifies bearer tokens issued by an external OIDC provider.

    Signing keys are resolved lazily and cached by the underlying
    :class:`jwt.PyJWKClient` (which itself caches keys and refreshes on unknown
    ``kid``). The first verification — or a verification after key rotation —
    incurs one network round-trip to the JWKS endpoint; it is run in a worker
    thread so the event loop is never blocked.
    """

    def __init__(self, config: Optional[SecurityConfig] = None) -> None:
        self._config = config or get_security_config()
        self._jwk_client: Optional[PyJWKClient] = None
        self._resolved_jwks_uri: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        """Whether OIDC verification is enabled and minimally configured."""
        c = self._config
        return bool(c.oidc_enabled and c.oidc_issuer and c.oidc_audience)

    def _jwks_uri(self) -> str:
        """Resolve the JWKS endpoint (explicit override or OIDC discovery).

        Sync (called inside a worker thread). Result is cached for the lifetime
        of the verifier so discovery happens at most once.
        """
        if self._config.oidc_jwks_url:
            return self._config.oidc_jwks_url
        if self._resolved_jwks_uri is not None:
            return self._resolved_jwks_uri
        issuer = (self._config.oidc_issuer or "").rstrip("/")
        # Provider JWKS paths vary (Okta/Azure/Keycloak each differ), so read the
        # standard discovery document rather than guessing a suffix.
        resp = httpx.get(f"{issuer}{_DISCOVERY_SUFFIX}", timeout=_DISCOVERY_TIMEOUT_S)
        resp.raise_for_status()
        jwks_uri = resp.json()["jwks_uri"]
        self._resolved_jwks_uri = str(jwks_uri)
        return self._resolved_jwks_uri

    def _get_jwk_client(self) -> PyJWKClient:
        if self._jwk_client is None:
            self._jwk_client = PyJWKClient(self._jwks_uri(), cache_keys=True)
        return self._jwk_client

    def _resolve_signing_key(self, token: str) -> Any:
        """Fetch the signing key for ``token`` (sync; offloaded by callers)."""
        return self._get_jwk_client().get_signing_key_from_jwt(token).key

    async def verify(self, token: str) -> AuthUser:
        """Verify an OIDC token and map its claims to an :class:`AuthUser`.

        Raises:
            InvalidTokenError: If OIDC is not configured, the signature/claims
                fail validation, or the signing key cannot be resolved.
        """
        if not self.is_configured:
            raise InvalidTokenError("OIDC verification is not configured")

        try:
            signing_key = await asyncio.to_thread(self._resolve_signing_key, token)
        except Exception as e:
            # Network/JWKS errors must not leak token bytes into logs.
            logger.warning("oidc_jwks_resolution_failed: %s", type(e).__name__)
            raise InvalidTokenError("Unable to resolve OIDC signing key") from e

        try:
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=self._config.oidc_algorithms,
                audience=self._config.oidc_audience,
                issuer=self._config.oidc_issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as e:
            raise InvalidTokenError("OIDC token has expired") from e
        except jwt.InvalidTokenError as e:
            raise InvalidTokenError(f"Invalid OIDC token: {e}") from e

        return self._build_user(payload)

    def _build_user(self, payload: dict[str, Any]) -> AuthUser:
        """Translate verified IdP claims into a framework identity."""
        c = self._config
        user_id = str(payload.get(c.oidc_username_claim) or payload.get("sub") or "")
        if not user_id:
            raise InvalidTokenError(
                f"OIDC token missing identity claim '{c.oidc_username_claim}'"
            )

        roles = self._map_roles(payload.get(c.oidc_roles_claim))
        scopes = self._extract_scopes(payload.get(c.oidc_scopes_claim))
        tenant_id = (
            str(payload.get(c.oidc_tenant_claim))
            if c.oidc_tenant_claim and payload.get(c.oidc_tenant_claim)
            else "default"
        )
        exp = payload.get("exp")
        return AuthUser(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=roles,
            scopes=scopes,
            email=payload.get("email"),
            token_id=payload.get("jti"),
            expires_at=(
                datetime.fromtimestamp(float(exp), tz=timezone.utc) if exp else None
            ),
            metadata=payload,
        )

    def _map_roles(self, raw: Any) -> set[AuthRole]:
        """Map IdP role strings to AuthRole via OIDC_ROLE_MAP, with a default."""
        idp_roles: list[str] = []
        if isinstance(raw, str):
            idp_roles = [raw]
        elif isinstance(raw, (list, tuple, set)):
            idp_roles = [str(r) for r in raw]

        mapped: set[AuthRole] = set()
        for idp_role in idp_roles:
            app_role = self._config.oidc_role_map.get(idp_role)
            if app_role is None:
                continue
            try:
                mapped.add(AuthRole(app_role))
            except ValueError:
                logger.warning("oidc_unknown_mapped_role: %s", app_role)

        if not mapped:
            try:
                mapped.add(AuthRole(self._config.oidc_default_role))
            except ValueError:
                mapped.add(AuthRole.USER)
        return mapped

    @staticmethod
    def _extract_scopes(raw: Any) -> set[str]:
        """Read scopes from a space-delimited OAuth string or a JSON array."""
        if isinstance(raw, str):
            return {s.strip().lower() for s in raw.split() if s.strip()}
        if isinstance(raw, (list, tuple, set)):
            return {str(s).strip().lower() for s in raw if str(s).strip()}
        return set()
