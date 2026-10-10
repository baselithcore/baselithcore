"""OAuth discovery: token, registration and revocation endpoints stay on the issuer's origin."""

from __future__ import annotations

import pytest

from baselith.core.mcp.oauth_client import MCPOAuthError, OAuthClient

from .fakes.oauth_server import ISSUER, FakeOAuth


@pytest.fixture(autouse=True)
def _allow(internal_ok: None) -> None:
    return None


def _client(fake: FakeOAuth) -> OAuthClient:
    return OAuthClient(httpx_transport=fake.transport())


@pytest.mark.parametrize(
    "key", ["token_endpoint", "registration_endpoint", "revocation_endpoint"]
)
async def test_an_endpoint_on_another_origin_is_refused(key: str) -> None:
    fake = FakeOAuth()
    fake.as_metadata[key] = "https://elsewhere.example.net/endpoint"
    with pytest.raises(MCPOAuthError, match="another origin"):
        await _client(fake).discover_authorization_server(ISSUER)


@pytest.mark.parametrize(
    "endpoint",
    [
        f"{ISSUER}:8443/token",  # another port is another origin
        ISSUER.replace("https://", "https://sub.") + "/token",  # another host
    ],
)
async def test_another_port_or_subdomain_is_another_origin(endpoint: str) -> None:
    fake = FakeOAuth()
    fake.as_metadata["token_endpoint"] = endpoint
    with pytest.raises(MCPOAuthError, match="another origin"):
        await _client(fake).discover_authorization_server(ISSUER)


async def test_the_same_origin_with_an_explicit_default_port_or_other_case_is_accepted() -> (
    None
):
    fake = FakeOAuth()
    host = ISSUER.removeprefix("https://")
    fake.as_metadata["token_endpoint"] = f"https://{host.upper()}:443/token"
    meta = await _client(fake).discover_authorization_server(ISSUER)
    assert meta.token_endpoint.endswith("/token")


async def test_the_caller_can_opt_in_for_an_issuer_it_trusts() -> None:
    fake = FakeOAuth()
    fake.as_metadata["token_endpoint"] = "https://tokens.example.net/token"
    meta = await _client(fake).discover_authorization_server(
        ISSUER, allow_cross_origin_endpoints=True
    )
    assert meta.token_endpoint == "https://tokens.example.net/token"
