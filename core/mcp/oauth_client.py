"""OAuth 2.1 client library for authenticating to third-party MCP servers.

A pure library: no storage, no UI, no redirect handling. The caller keeps the
:class:`~core.mcp.oauth_types.AuthorizationRequest` (state + PKCE verifier) and
the :class:`~core.mcp.oauth_types.TokenSet`, and passes the access token to
``MCPClient(auth=...)``. What it implements:

* RFC 9728 protected-resource metadata discovery, from the challenge's
  ``resource_metadata`` URL or the ``/.well-known/oauth-protected-resource``
  location (path-inserted first, then the host root). The metadata's
  ``resource`` must equal the resource we asked about.
* RFC 8414 authorization-server metadata discovery (with the OpenID Connect
  well-known locations as fallback); the ``issuer`` must equal the one asked
  for, ``S256`` must be advertised as a PKCE method, and the token,
  registration and revocation endpoints (where codes, refresh tokens and
  client secrets are posted) must share the issuer's origin unless the caller
  opts in for that issuer.
* RFC 7591 dynamic client registration, optional.
* RFC 8707 ``resource`` on the authorization request, the code exchange and
  every refresh, so a token is bound to one MCP server.
* PKCE ``S256`` (RFC 7636), authorization-URL building, code exchange, refresh.

Every network call goes through the SSRF-guarded hardened client with a
deadline and a response-size cap, never follows a redirect, requires ``https``
(loopback excepted under ``MCP_ALLOW_INTERNAL_ENDPOINTS``), and fails with a
typed error that carries no raw response body.
"""

from __future__ import annotations

import base64
import re
import secrets
import time
from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from pydantic import SecretStr

from core.mcp.client_errors import bounded
from core.mcp.oauth_helpers import generate_pkce
from core.mcp.oauth_helpers import origin as _origin
from core.mcp.oauth_helpers import secret as _secret
from core.mcp.oauth_helpers import strs as _strs
from core.mcp.oauth_helpers import well_known as _well_known
from core.mcp.oauth_http import OAuthHTTP
from core.mcp.oauth_types import (
    AuthorizationRequest,
    AuthorizationServerMetadata,
    ClientRegistration,
    MCPOAuthError,
    ProtectedResourceMetadata,
    TokenSet,
)

_ERROR_CODE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_WELL_KNOWN_PR = "/.well-known/oauth-protected-resource"
_WELL_KNOWN_AS = "/.well-known/oauth-authorization-server"
_WELL_KNOWN_OIDC = "/.well-known/openid-configuration"


class OAuthClient:
    """Discovery, registration and token calls for one MCP OAuth flow."""

    def __init__(
        self,
        *,
        httpx_transport: httpx.AsyncBaseTransport | None = None,
        timeout: float | None = None,
        max_response_bytes: int | None = None,
        allowed_hosts: Iterable[str] | None = None,
    ) -> None:
        """
        Args:
            httpx_transport: httpx transport override (in-process testing).
            timeout: Deadline, in seconds, for each network call. Defaults to
                ``MCP_OAUTH_TIMEOUT_SECONDS``.
            max_response_bytes: Cap on one response body. Defaults to
                ``MCP_OAUTH_MAX_RESPONSE_BYTES``.
            allowed_hosts: When given, only these exact hostnames may be
                contacted (enforced by the SSRF policy).
        """
        self._http = OAuthHTTP(
            httpx_transport=httpx_transport,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            allowed_hosts=allowed_hosts,
        )

    async def aclose(self) -> None:
        """Release the HTTP connection pool."""
        await self._http.aclose()

    # ------------------------------------------------------------------ RFC 9728

    async def discover_protected_resource(
        self, resource_url: str, *, resource_metadata_url: str | None = None
    ) -> ProtectedResourceMetadata:
        """Fetch the protected-resource metadata of an MCP server.

        Args:
            resource_url: The MCP endpoint URL (the RFC 8707 resource).
            resource_metadata_url: The ``resource_metadata`` URL from the 401
                challenge (``MCPAuthError.resource_metadata``); when absent
                the well-known locations are tried.

        Raises:
            MCPOAuthError: Missing/invalid metadata, or ``resource`` differs
                from *resource_url* (a metadata document for someone else).
        """
        if resource_metadata_url:
            candidates = [resource_metadata_url]
        else:
            parsed = urlparse(resource_url)
            candidates = [
                _well_known(resource_url, _WELL_KNOWN_PR),
                f"{parsed.scheme}://{parsed.netloc}{_WELL_KNOWN_PR}",
            ]
        raw = await self._http.fetch_metadata(candidates, "protected-resource metadata")
        if raw.get("resource") != resource_url:
            raise MCPOAuthError(
                "protected-resource metadata 'resource' does not match the "
                "requested resource"
            )
        servers = _strs(raw.get("authorization_servers"))
        if not servers:
            raise MCPOAuthError(
                "protected-resource metadata lists no authorization_servers"
            )
        return ProtectedResourceMetadata(
            resource=resource_url,
            authorization_servers=servers,
            scopes_supported=_strs(raw.get("scopes_supported")),
            bearer_methods_supported=_strs(raw.get("bearer_methods_supported")),
            raw=raw,
        )

    # ------------------------------------------------------------------ RFC 8414

    async def discover_authorization_server(
        self, issuer: str, *, allow_cross_origin_endpoints: bool = False
    ) -> AuthorizationServerMetadata:
        """Fetch and validate an authorization server's metadata.

        Tries the RFC 8414 location, then the OpenID Connect ones.

        Args:
            issuer: The issuer identifier (an ``https`` URL).
            allow_cross_origin_endpoints: Accept a token, registration or
                revocation endpoint on another origin than the issuer. Off by
                default: a metadata document must not be able to send an
                authorization code, a refresh token or a client secret to a
                host the caller never vetted. Turn it on only for an issuer the
                operator explicitly trusts to split its endpoints across hosts.

        Raises:
            MCPOAuthError: Metadata missing, ``issuer`` mismatch, an endpoint
                that is not ``https`` (or on another origin, see above), or no
                ``S256`` PKCE support.
        """
        self._http.require_https(issuer)
        parsed = urlparse(issuer)
        root = f"{parsed.scheme}://{parsed.netloc}"
        raw = await self._http.fetch_metadata(
            [
                _well_known(issuer, _WELL_KNOWN_AS),
                _well_known(issuer, _WELL_KNOWN_OIDC),
                f"{issuer.rstrip('/')}{_WELL_KNOWN_OIDC}",
                f"{root}{_WELL_KNOWN_OIDC}",
            ],
            "authorization-server metadata",
        )
        if raw.get("issuer") != issuer:
            raise MCPOAuthError("authorization-server metadata 'issuer' mismatch")
        endpoints: dict[str, str | None] = {}
        for key in ("authorization_endpoint", "token_endpoint"):
            value = raw.get(key)
            if not isinstance(value, str):
                raise MCPOAuthError(f"authorization-server metadata lacks '{key}'")
            self._http.require_https(value)
            if key == "token_endpoint" and not allow_cross_origin_endpoints:
                self._require_same_origin(issuer, key, value)
            endpoints[key] = value
        for key in ("registration_endpoint", "revocation_endpoint"):
            value = raw.get(key)
            if value is not None:
                if not isinstance(value, str):
                    raise MCPOAuthError(f"authorization-server '{key}' is invalid")
                self._http.require_https(value)
                if not allow_cross_origin_endpoints:
                    self._require_same_origin(issuer, key, value)
            endpoints[key] = value
        methods = _strs(raw.get("code_challenge_methods_supported"))
        if "S256" not in methods:
            raise MCPOAuthError(
                "authorization server does not advertise PKCE S256; refusing"
            )
        return AuthorizationServerMetadata(
            issuer=issuer,
            authorization_endpoint=str(endpoints["authorization_endpoint"]),
            token_endpoint=str(endpoints["token_endpoint"]),
            registration_endpoint=endpoints["registration_endpoint"],
            revocation_endpoint=endpoints["revocation_endpoint"],
            scopes_supported=_strs(raw.get("scopes_supported")),
            code_challenge_methods_supported=methods,
            token_endpoint_auth_methods_supported=_strs(
                raw.get("token_endpoint_auth_methods_supported")
            ),
            grant_types_supported=_strs(raw.get("grant_types_supported")),
        )

    @staticmethod
    def _require_same_origin(issuer: str, key: str, endpoint: str) -> None:
        if _origin(endpoint) != _origin(issuer):
            raise MCPOAuthError(
                f"authorization-server '{key}' is on another origin than the issuer; refusing"
            )

    # ------------------------------------------------------------------ RFC 7591

    async def register_client(
        self,
        server: AuthorizationServerMetadata,
        *,
        client_name: str,
        redirect_uris: Sequence[str],
        scope: str | None = None,
        token_endpoint_auth_method: str = "none",  # noqa: S107
    ) -> ClientRegistration:
        """Dynamically register a client (RFC 7591).

        Raises:
            MCPOAuthError: The server offers no registration endpoint, refused,
                or answered without a ``client_id``.
        """
        if server.registration_endpoint is None:
            raise MCPOAuthError(
                "authorization server offers no dynamic client registration endpoint"
            )
        body: dict[str, Any] = {
            "client_name": client_name,
            "redirect_uris": list(redirect_uris),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": token_endpoint_auth_method,
        }
        if scope:
            body["scope"] = scope
        status, raw = await self._http.request(
            "POST", server.registration_endpoint, json_body=body
        )
        parsed = self._http.parse_object(raw, "client registration response")
        if status not in (200, 201):
            raise self._token_error("client registration", status, parsed)
        client_id = parsed.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise MCPOAuthError("client registration response lacks client_id")
        secret = parsed.get("client_secret")
        expires = parsed.get("client_secret_expires_at")
        return ClientRegistration(
            client_id=SecretStr(client_id),
            client_secret=SecretStr(secret)
            if isinstance(secret, str) and secret
            else None,
            client_secret_expires_at=expires if isinstance(expires, int) else None,
            redirect_uris=_strs(parsed.get("redirect_uris")) or tuple(redirect_uris),
            token_endpoint_auth_method=str(
                parsed.get("token_endpoint_auth_method", token_endpoint_auth_method)
            ),
        )

    # ------------------------------------------------- authorization (RFC 7636/8707)

    @staticmethod
    def _canonical_resource(resource: str) -> str:
        parsed = urlparse(resource)
        if not parsed.scheme or not parsed.netloc or parsed.fragment:
            raise MCPOAuthError(
                "invalid resource indicator: must be an absolute URI without a fragment"
            )
        return resource

    def build_authorization_request(
        self,
        server: AuthorizationServerMetadata,
        *,
        client_id: str | SecretStr,
        redirect_uri: str,
        resource: str,
        scopes: Sequence[str] = (),
        state: str | None = None,
    ) -> AuthorizationRequest:
        """Build the authorization URL with PKCE S256 and the ``resource`` param."""
        pkce = generate_pkce()
        state = state or secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": _secret(client_id),
            "redirect_uri": redirect_uri,
            "code_challenge": pkce.challenge,
            "code_challenge_method": pkce.method,
            "state": state,
            "resource": self._canonical_resource(resource),
        }
        if scopes:
            params["scope"] = " ".join(scopes)
        base = server.authorization_endpoint
        sep = "&" if "?" in base else "?"
        return AuthorizationRequest(
            url=f"{base}{sep}{urlencode(params)}",
            state=state,
            code_verifier=pkce.verifier,
        )

    # ------------------------------------------------------------ token endpoint

    @staticmethod
    def _token_error(what: str, status: int, parsed: dict[str, Any]) -> MCPOAuthError:
        code = parsed.get("error")
        error = code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else None
        description = parsed.get("error_description")
        return MCPOAuthError(
            f"OAuth {what} failed (HTTP {status}"
            + (f", {error}" if error else "")
            + ")",
            error=error,
            error_description=bounded(description)
            if isinstance(description, str)
            else None,
        )

    async def _token_request(
        self,
        server: AuthorizationServerMetadata,
        form: dict[str, str],
        *,
        client_id: str,
        client_secret: str | None,
    ) -> TokenSet:
        headers: dict[str, str] = {}
        methods = server.token_endpoint_auth_methods_supported
        if client_secret is None:
            form["client_id"] = client_id
        elif not methods or "client_secret_basic" in methods:
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            headers["Authorization"] = f"Basic {basic}"
        else:
            form["client_id"] = client_id
            form["client_secret"] = client_secret
        status, raw = await self._http.request(
            "POST", server.token_endpoint, data=form, headers=headers
        )
        parsed = self._http.parse_object(raw, "token response")
        if status != 200:
            raise self._token_error("token request", status, parsed)
        return self._parse_tokens(parsed)

    @staticmethod
    def _parse_tokens(parsed: dict[str, Any]) -> TokenSet:
        access = parsed.get("access_token")
        if not isinstance(access, str) or not access:
            raise MCPOAuthError("token response lacks access_token")
        token_type = parsed.get("token_type")
        if not isinstance(token_type, str) or token_type.lower() != "bearer":
            raise MCPOAuthError("token response token_type is not Bearer")
        expires_in = parsed.get("expires_in")
        lifetime = (
            int(expires_in)
            if isinstance(expires_in, (int, float))
            and not isinstance(expires_in, bool)
            and expires_in > 0
            else None
        )
        refresh = parsed.get("refresh_token")
        scope = parsed.get("scope")
        return TokenSet(
            access_token=SecretStr(access),
            token_type="Bearer",  # noqa: S106
            refresh_token=SecretStr(refresh)
            if isinstance(refresh, str) and refresh
            else None,
            expires_in=lifetime,
            expires_at=time.time() + lifetime if lifetime is not None else None,
            scope=scope if isinstance(scope, str) else None,
        )

    async def exchange_code(
        self,
        server: AuthorizationServerMetadata,
        *,
        client_id: str | SecretStr,
        redirect_uri: str,
        code: str,
        code_verifier: str | SecretStr,
        resource: str,
        client_secret: str | SecretStr | None = None,
    ) -> TokenSet:
        """Exchange an authorization code (PKCE verifier + RFC 8707 resource)."""
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": _secret(code_verifier),
            "resource": self._canonical_resource(resource),
        }
        return await self._token_request(
            server,
            form,
            client_id=_secret(client_id),
            client_secret=_secret(client_secret) if client_secret is not None else None,
        )

    async def refresh(
        self,
        server: AuthorizationServerMetadata,
        *,
        client_id: str | SecretStr,
        refresh_token: str | SecretStr,
        resource: str,
        client_secret: str | SecretStr | None = None,
        scopes: Sequence[str] = (),
    ) -> TokenSet:
        """Refresh an access token, re-asserting the RFC 8707 resource."""
        form = {
            "grant_type": "refresh_token",
            "refresh_token": _secret(refresh_token),
            "resource": self._canonical_resource(resource),
        }
        if scopes:
            form["scope"] = " ".join(scopes)
        return await self._token_request(
            server,
            form,
            client_id=_secret(client_id),
            client_secret=_secret(client_secret) if client_secret is not None else None,
        )

    # ------------------------------------------------------------- revocation (RFC 7009)

    async def revoke_token(
        self,
        server: AuthorizationServerMetadata,
        *,
        token: str | SecretStr,
        client_id: str | SecretStr,
        client_secret: str | SecretStr | None = None,
        token_type_hint: str = "refresh_token",  # noqa: S107
    ) -> bool:
        """Ask the authorization server to revoke a token (RFC 7009).

        Best effort by design: a caller disconnecting a user deletes its own copy
        regardless. Nothing about the server's answer is surfaced beyond success.

        Returns:
            ``True`` when the server accepted the request (HTTP 200), ``False``
            when it offers no revocation endpoint or refused.

        Raises:
            MCPTransportError: The server could not be reached (or answered with
                a redirect, which is never followed).
        """
        if server.revocation_endpoint is None:
            return False
        form = {"token": _secret(token), "token_type_hint": token_type_hint}
        headers: dict[str, str] = {}
        if client_secret is None:
            form["client_id"] = _secret(client_id)
        else:
            basic = base64.b64encode(
                f"{_secret(client_id)}:{_secret(client_secret)}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {basic}"
        status, _ = await self._http.request(
            "POST", server.revocation_endpoint, data=form, headers=headers
        )
        return status == 200


__all__ = ["MCPOAuthError", "OAuthClient", "generate_pkce"]
