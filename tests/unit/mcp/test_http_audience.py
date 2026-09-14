"""Audience/resource binding on the MCP Streamable HTTP endpoint (RFC 8707).

The endpoint published an RFC 9728 ``resource`` identifier and then accepted
any token the ``AuthManager`` would verify — including one an authorization
server minted for a *different* resource. That is the confused-deputy shape
RFC 8707 exists to close: a token stolen from (or legitimately issued for)
another service replayed here reached the whole tool catalog.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.mcp.http_authz import resource_identifier, token_audience_rejected

from .test_http_transport import (
    _app,
    _asgi_client,
    _config,
    _initialize_msg,
    _StubAuthManager,
)


def _bearer_user(user_id: str = "u-1", *, aud=None, scopes=("mcp:invoke",)):
    metadata = {} if aud is None else {"aud": aud}
    return SimpleNamespace(
        user_id=user_id,
        is_authenticated=True,
        tenant_id="default",
        has_scope=lambda scope: scope in scopes,
        metadata=metadata,
    )


def _install(monkeypatch, user):
    import core.auth.manager as auth_manager_module

    monkeypatch.setattr(
        auth_manager_module, "get_auth_manager", lambda: _StubAuthManager(user)
    )


async def _post(config, *, token="t", scheme="Bearer"):
    """One admitted-or-not request. ``initialize`` needs no prior session."""
    async with _asgi_client(_app(config)) as client:
        return await client.post(
            "/mcp",
            json=_initialize_msg(),
            headers={"Authorization": f"{scheme} {token}"},
        )


def _enforcing(**overrides):
    """A config with the audience check explicitly on.

    The setting resolves to the production posture when unset, and the test
    environment is not production — so an enforcement test that left it unset
    would pass without ever reaching the check.
    """
    return _config(
        mcp_http_require_auth=True, mcp_require_token_audience=True, **overrides
    )


class TestMismatch:
    async def test_token_for_another_resource_is_refused(self, monkeypatch) -> None:
        _install(monkeypatch, _bearer_user(aud="https://other.example/api"))
        response = await _post(_enforcing())

        assert response.status_code == 401
        assert response.json()["error"]["code"] == -32001
        assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]

    async def test_matching_audience_is_admitted(self, monkeypatch) -> None:
        _install(monkeypatch, _bearer_user(aud="http://mcp.test/mcp"))
        response = await _post(_enforcing())

        assert response.status_code == 200

    async def test_origin_only_audience_is_admitted(self, monkeypatch) -> None:
        """The MCP spec lists the bare origin as a valid canonical resource URI."""
        _install(monkeypatch, _bearer_user(aud="http://mcp.test"))
        response = await _post(_enforcing())

        assert response.status_code == 200

    async def test_audience_list_matching_one_entry_is_admitted(
        self, monkeypatch
    ) -> None:
        _install(
            monkeypatch,
            _bearer_user(aud=["https://other.example", "http://mcp.test/mcp"]),
        )
        response = await _post(_enforcing())

        assert response.status_code == 200


class TestMissingAudience:
    async def test_audience_less_token_passes_when_not_required(
        self, monkeypatch
    ) -> None:
        _install(monkeypatch, _bearer_user(aud=None))
        config = _config(mcp_http_require_auth=True, mcp_require_token_audience=False)
        response = await _post(config)

        assert response.status_code == 200

    async def test_audience_less_token_is_refused_when_required(
        self, monkeypatch
    ) -> None:
        _install(monkeypatch, _bearer_user(aud=None))
        config = _config(mcp_http_require_auth=True, mcp_require_token_audience=True)
        response = await _post(config)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == -32001

    async def test_requirement_defaults_to_the_production_posture(
        self, monkeypatch
    ) -> None:
        """Unset resolves at request time: required in production, not elsewhere."""
        import core.mcp.http_authz as http_authz

        _install(monkeypatch, _bearer_user(aud=None))
        config = _config(mcp_http_require_auth=True)  # setting absent entirely

        monkeypatch.setattr(http_authz, "is_production_env", lambda: False)
        assert (await _post(config)).status_code == 200

        monkeypatch.setattr(http_authz, "is_production_env", lambda: True)
        assert (await _post(config)).status_code == 401


class TestOperatorOverride:
    async def test_disabling_the_check_also_stands_down_the_mismatch(
        self, monkeypatch
    ) -> None:
        """JWT_AUDIENCE is pinned per deployment, so a mismatch is binary: every
        token is wrong, and without an override the endpoint is unreachable."""
        _install(monkeypatch, _bearer_user(aud="https://other.example/api"))
        config = _config(mcp_http_require_auth=True, mcp_require_token_audience=False)

        assert (await _post(config)).status_code == 200

    async def test_production_default_still_enforces_the_mismatch(
        self, monkeypatch
    ) -> None:
        import core.mcp.http_authz as http_authz

        _install(monkeypatch, _bearer_user(aud="https://other.example/api"))
        monkeypatch.setattr(http_authz, "is_production_env", lambda: True)

        assert (await _post(_config(mcp_http_require_auth=True))).status_code == 401


class TestConfiguredResourceUrl:
    async def test_configured_resource_is_the_enforced_value(self, monkeypatch) -> None:
        """request.base_url comes from the Host header; behind a proxy that does
        not pin it, the audience would be checked against whatever a caller
        claimed the host was."""
        _install(monkeypatch, _bearer_user(aud="https://api.example.com/mcp"))
        config = _enforcing(mcp_resource_url="https://api.example.com/mcp")

        # The request arrives at http://mcp.test/mcp; only the configured
        # canonical identifier is accepted.
        assert (await _post(config)).status_code == 200

    async def test_host_header_audience_is_refused_when_configured(
        self, monkeypatch
    ) -> None:
        _install(monkeypatch, _bearer_user(aud="http://mcp.test/mcp"))
        config = _enforcing(mcp_resource_url="https://api.example.com/mcp")

        assert (await _post(config)).status_code == 401

    def test_configured_value_wins_over_base_url(self) -> None:
        request = SimpleNamespace(base_url="http://mcp.test/")
        cfg = SimpleNamespace(mcp_resource_url="https://api.example.com/mcp/")

        assert (
            resource_identifier(request, "/mcp", cfg) == "https://api.example.com/mcp"
        )

    def test_base_url_is_the_fallback(self) -> None:
        request = SimpleNamespace(base_url="http://mcp.test/")
        cfg = SimpleNamespace(mcp_resource_url="")

        assert resource_identifier(request, "/mcp", cfg) == "http://mcp.test/mcp"

    def test_published_metadata_matches_the_enforced_value(self) -> None:
        """The advertised `resource` and the enforced one are one value."""
        from fastapi.testclient import TestClient

        config = _config(
            mcp_http_require_auth=True,
            mcp_resource_url="https://api.example.com/mcp",
        )
        client = TestClient(_app(config))
        document = client.get("/.well-known/oauth-protected-resource/mcp").json()

        assert document["resource"] == "https://api.example.com/mcp"


class TestApiKeysAreExempt:
    async def test_api_key_without_audience_is_admitted_in_production(
        self, monkeypatch
    ) -> None:
        """An API key is not an OAuth token and carries no audience to bind."""
        import core.mcp.http_authz as http_authz

        _install(monkeypatch, _bearer_user(aud=None))
        monkeypatch.setattr(http_authz, "is_production_env", lambda: True)
        config = _config(mcp_http_require_auth=True)
        response = await _post(config, scheme="ApiKey")

        assert response.status_code == 200


class TestHelpers:
    def test_resource_identifier_matches_the_published_metadata(self) -> None:
        request = SimpleNamespace(base_url="http://mcp.test/")
        assert resource_identifier(request, "/mcp") == "http://mcp.test/mcp"

    def test_resource_identifier_without_config_uses_base_url(self) -> None:
        request = SimpleNamespace(base_url="http://mcp.test/")
        assert resource_identifier(request, "/mcp") == "http://mcp.test/mcp"

    @pytest.mark.parametrize(
        ("aud", "rejected"),
        [
            ("http://mcp.test/mcp", False),
            ("http://mcp.test/mcp/", False),
            ("HTTP://MCP.TEST/mcp", False),
            ("http://mcp.test", False),
            ("http://mcp.test/other", True),
            ("", True),
        ],
    )
    def test_audience_comparison_is_canonicalized(self, aud, rejected) -> None:
        user = _bearer_user(aud=aud)
        assert (
            token_audience_rejected(user, "http://mcp.test/mcp", required=True)
            is rejected
        )
