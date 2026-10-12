"""A fake protected resource + authorization server over httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import httpx

RESOURCE = "https://mcp.example.com/mcp"
ISSUER = "https://auth.example.com"
ISSUER_WITH_PATH = "https://auth.example.com/tenant1"


class FakeOAuth:
    """Serves RFC 9728 / 8414 / 7591 / 6749 endpoints and records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.form: dict[str, list[str]] = {}
        self.json_body: Any = None
        self.pr_metadata: dict[str, Any] | None = {
            "resource": RESOURCE,
            "authorization_servers": [ISSUER],
            "scopes_supported": ["read", "write"],
        }
        self.as_metadata: dict[str, Any] = self.default_as(ISSUER)
        self.token_response: dict[str, Any] = {
            "access_token": "at-123",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "rt-456",
            "scope": "read",
        }
        self.token_status = 200
        self.register_status = 201
        self.available: set[str] = set()  # empty = every route answers

    @staticmethod
    def default_as(issuer: str) -> dict[str, Any]:
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "registration_endpoint": f"{issuer}/register",
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/.well-known/oauth-protected-resource"):
            if self.pr_metadata is None:
                return httpx.Response(404)
            return httpx.Response(200, json=self.pr_metadata)
        if path.startswith("/.well-known/oauth-authorization-server"):
            if (
                "oauth-authorization-server" in self.available - {"*"}
                or not self.available
            ):
                return httpx.Response(200, json=self.as_metadata)
            return httpx.Response(404)
        if path.startswith("/.well-known/openid-configuration") or path.endswith(
            "/.well-known/openid-configuration"
        ):
            if "openid" in self.available:
                return httpx.Response(200, json=self.as_metadata)
            return httpx.Response(404)
        if path.endswith("/register"):
            self.json_body = json.loads(request.content)
            body = {
                "client_id": "dyn-client-1",
                "client_secret": "dyn-secret-1",
                **{
                    k: self.json_body[k]
                    for k in ("redirect_uris", "client_name")
                    if k in self.json_body
                },
            }
            return httpx.Response(self.register_status, json=body)
        if path.endswith("/token"):
            self.form = parse_qs(request.content.decode())
            return httpx.Response(self.token_status, json=self.token_response)
        return httpx.Response(404)
