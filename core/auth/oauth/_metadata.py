"""RFC 8414 authorization server metadata.

MCP clients discover this document, not OpenID Connect's
``openid-configuration``, which is why the OIDC document is absent by design.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.auth.oauth._models import GrantType

#: Mount point of the plugin's OAuth routes, relative to the issuer.
_OAUTH_BASE = "/api/auth/oauth"


def build_metadata_document(
    *,
    issuer: str,
    grant_types: Iterable[GrantType],
    scopes: Iterable[str],
) -> dict[str, object]:
    """Build the ``/.well-known/oauth-authorization-server`` document.

    Args:
        issuer: The issuer identifier, without a trailing slash.
        grant_types: Grants this deployment has enabled.
        scopes: The scope catalog to advertise.

    Returns:
        The metadata document, ready to serialize as JSON.
    """
    base = issuer.rstrip("/")
    enabled = sorted(g.value for g in grant_types)
    doc: dict[str, object] = {
        "issuer": base,
        "authorization_endpoint": f"{base}{_OAUTH_BASE}/authorize",
        "token_endpoint": f"{base}{_OAUTH_BASE}/token",
        "revocation_endpoint": f"{base}{_OAUTH_BASE}/revoke",
        "introspection_endpoint": f"{base}{_OAUTH_BASE}/introspect",
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "grant_types_supported": enabled,
        # Only the code flow. The implicit flow ("token") and the password
        # grant are removed by OAuth 2.1 and are not implemented.
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
            "none",
        ],
        "scopes_supported": sorted(scopes),
        "service_documentation": f"{base}/auth/docs/guide/oauth-clients",
    }
    if GrantType.DEVICE_CODE.value in enabled:
        doc["device_authorization_endpoint"] = (
            f"{base}{_OAUTH_BASE}/device_authorization"
        )
    return doc
