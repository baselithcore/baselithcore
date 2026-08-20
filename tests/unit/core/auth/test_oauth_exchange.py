"""RFC 8693 token exchange — pure protocol rules, no I/O."""

from __future__ import annotations

import pytest

from core.auth.oauth import (
    ACCESS_TOKEN_TYPE,
    ClientType,
    GrantType,
    InvalidClientError,
    InvalidGrantError,
    InvalidRequestError,
    InvalidScopeError,
    InvalidTargetError,
    OAuthClient,
    SubjectTokenContext,
    TokenExchangeRequest,
    UnauthorizedClientError,
    build_actor_claim,
    build_may_act_claim,
    resolve_exchange_scope,
    validate_delegation,
    validate_exchange_request,
)


def test_token_exchange_grant_type_uses_the_rfc_urn() -> None:
    assert GrantType.TOKEN_EXCHANGE.value == (
        "urn:ietf:params:oauth:grant-type:token-exchange"
    )


def test_access_token_type_uses_the_rfc_urn() -> None:
    assert ACCESS_TOKEN_TYPE == "urn:ietf:params:oauth:token-type:access_token"


def test_invalid_target_is_a_400_with_the_rfc_code() -> None:
    exc = InvalidTargetError("unknown resource")
    assert exc.error == "invalid_target"
    assert exc.status_code == 400
    assert exc.to_dict() == {
        "error": "invalid_target",
        "error_description": "unknown resource",
    }


def test_a_client_has_no_authorized_actors_by_default() -> None:
    client = OAuthClient(
        client_id="bsc_1",
        client_type=ClientType.CONFIDENTIAL,
        redirect_uris=(),
        grant_types=frozenset({GrantType.AUTHORIZATION_CODE}),
        allowed_scopes=frozenset({"chat:read"}),
    )
    assert client.allowed_actors == frozenset()


def test_request_and_subject_context_are_hashable_value_types() -> None:
    request = TokenExchangeRequest(
        subject_token="tok",
        subject_token_type=ACCESS_TOKEN_TYPE,
        requested_token_type=None,
        scope=frozenset({"chat:read"}),
        resource=None,
    )
    subject = SubjectTokenContext(
        subject="user-1",
        client_id="bsc_1",
        scope=frozenset({"chat:read", "chat:write"}),
        tenant_id="acme",
        audience="https://mcp.example",
        has_actor=False,
    )
    assert {request, subject}  # frozen dataclasses are hashable


_AGENT = OAuthClient(
    client_id="bsc_agent",
    client_type=ClientType.CONFIDENTIAL,
    redirect_uris=(),
    grant_types=frozenset({GrantType.TOKEN_EXCHANGE}),
    allowed_scopes=frozenset({"chat:read"}),
    tenant_id="acme",
)


def _request(**overrides) -> TokenExchangeRequest:
    fields = {
        "subject_token": "tok",
        "subject_token_type": ACCESS_TOKEN_TYPE,
        "requested_token_type": None,
        "scope": frozenset({"chat:read"}),
        "resource": None,
    }
    fields.update(overrides)
    return TokenExchangeRequest(**fields)


def _subject(**overrides) -> SubjectTokenContext:
    fields = {
        "subject": "user-1",
        "client_id": "bsc_app",
        "scope": frozenset({"chat:read", "chat:write"}),
        "tenant_id": "acme",
        "audience": None,
        "has_actor": False,
    }
    fields.update(overrides)
    return SubjectTokenContext(**fields)


def test_a_public_actor_client_may_not_delegate() -> None:
    public_agent = OAuthClient(
        client_id="bsc_agent",
        client_type=ClientType.PUBLIC,
        redirect_uris=(),
        grant_types=frozenset({GrantType.TOKEN_EXCHANGE}),
        allowed_scopes=frozenset({"chat:read"}),
    )
    with pytest.raises(InvalidClientError):
        validate_exchange_request(public_agent, _request())


def test_the_actor_must_be_registered_for_the_grant() -> None:
    unregistered = OAuthClient(
        client_id="bsc_agent",
        client_type=ClientType.CONFIDENTIAL,
        redirect_uris=(),
        grant_types=frozenset({GrantType.CLIENT_CREDENTIALS}),
        allowed_scopes=frozenset({"chat:read"}),
    )
    with pytest.raises(UnauthorizedClientError):
        validate_exchange_request(unregistered, _request())


def test_an_unsupported_subject_token_type_is_refused() -> None:
    with pytest.raises(InvalidRequestError):
        validate_exchange_request(
            _AGENT,
            _request(subject_token_type="urn:ietf:params:oauth:token-type:id_token"),
        )


def test_only_an_access_token_may_be_requested() -> None:
    with pytest.raises(InvalidRequestError):
        validate_exchange_request(
            _AGENT,
            _request(
                requested_token_type="urn:ietf:params:oauth:token-type:refresh_token"
            ),
        )


def test_the_requested_token_type_may_be_omitted() -> None:
    validate_exchange_request(_AGENT, _request(requested_token_type=None))
    validate_exchange_request(_AGENT, _request(requested_token_type=ACCESS_TOKEN_TYPE))


def test_scope_is_required() -> None:
    with pytest.raises(InvalidRequestError):
        validate_exchange_request(_AGENT, _request(scope=frozenset()))


def test_an_already_delegated_token_cannot_be_exchanged_again() -> None:
    with pytest.raises(InvalidGrantError):
        validate_delegation(
            _subject(has_actor=True),
            actor=_AGENT,
            subject_client_actors=frozenset({"bsc_agent"}),
        )


def test_a_token_without_a_user_subject_cannot_be_delegated() -> None:
    # client_credentials: sub equals the client id, so there is no user.
    with pytest.raises(InvalidGrantError):
        validate_delegation(
            _subject(subject="bsc_app"),
            actor=_AGENT,
            subject_client_actors=frozenset({"bsc_agent"}),
        )


def test_an_actor_outside_the_allowlist_is_refused() -> None:
    with pytest.raises(UnauthorizedClientError):
        validate_delegation(
            _subject(), actor=_AGENT, subject_client_actors=frozenset({"bsc_other"})
        )


def test_delegation_never_crosses_a_tenant() -> None:
    with pytest.raises(InvalidGrantError):
        validate_delegation(
            _subject(tenant_id="other"),
            actor=_AGENT,
            subject_client_actors=frozenset({"bsc_agent"}),
        )


def test_a_permitted_delegation_passes() -> None:
    validate_delegation(
        _subject(), actor=_AGENT, subject_client_actors=frozenset({"bsc_agent"})
    )


def test_a_tenantless_actor_may_act_for_a_tenanted_subject() -> None:
    # A client registered with no tenant_id is unscoped by design (the same
    # convention routes/_authorize.py already applies to authorization), so
    # it may act for a subject token that does carry a tenant.
    tenantless_agent = OAuthClient(
        client_id="bsc_agent",
        client_type=ClientType.CONFIDENTIAL,
        redirect_uris=(),
        grant_types=frozenset({GrantType.TOKEN_EXCHANGE}),
        allowed_scopes=frozenset({"chat:read"}),
    )
    validate_delegation(
        _subject(tenant_id="acme"),
        actor=tenantless_agent,
        subject_client_actors=frozenset({"bsc_agent"}),
    )


def test_scope_is_the_three_way_intersection() -> None:
    granted = resolve_exchange_scope(
        requested=frozenset({"chat:read", "chat:write"}),
        subject_scope=frozenset({"chat:read", "chat:write"}),
        actor_allowed=frozenset({"chat:read"}),
    )
    assert granted == frozenset({"chat:read"})


def test_a_scope_the_subject_token_lacks_is_dropped_not_an_error() -> None:
    granted = resolve_exchange_scope(
        requested=frozenset({"chat:read", "memory:write"}),
        subject_scope=frozenset({"chat:read"}),
        actor_allowed=frozenset({"chat:read", "memory:write"}),
    )
    assert granted == frozenset({"chat:read"})


def test_an_empty_intersection_is_an_error_not_a_powerless_token() -> None:
    with pytest.raises(InvalidScopeError):
        resolve_exchange_scope(
            requested=frozenset({"memory:write"}),
            subject_scope=frozenset({"chat:read"}),
            actor_allowed=frozenset({"memory:write"}),
        )


def test_the_actor_claim_names_the_agent_as_a_client() -> None:
    assert build_actor_claim("bsc_agent") == {
        "sub": "bsc_agent",
        "client_id": "bsc_agent",
    }


def test_may_act_lists_the_allowlist_and_is_absent_when_empty() -> None:
    assert build_may_act_claim(frozenset({"bsc_b", "bsc_a"})) == {
        "client_id": ["bsc_a", "bsc_b"]
    }
    assert build_may_act_claim(frozenset()) is None


# --------------------------------------------------------------------------- #
# Rule 10 — RFC 8693 §2.1/§2.2.2 target (resource/audience) validation
# --------------------------------------------------------------------------- #

from core.auth.oauth import resolve_exchange_target  # noqa: E402


def _target_actor(resources: frozenset[str] = frozenset()) -> OAuthClient:
    return OAuthClient(
        client_id="agent-1",
        client_type=ClientType.CONFIDENTIAL,
        redirect_uris=(),
        grant_types=frozenset({GrantType.TOKEN_EXCHANGE}),
        allowed_scopes=frozenset({"chat:read"}),
        allowed_resources=resources,
    )


def _target_subject(audience: str | None = None) -> SubjectTokenContext:
    return SubjectTokenContext(
        subject="alice",
        client_id="web-app",
        scope=frozenset({"chat:read"}),
        tenant_id="t1",
        audience=audience,
        has_actor=False,
    )


def _target_request(resource: str | None) -> TokenExchangeRequest:
    return TokenExchangeRequest(
        subject_token="tok",
        subject_token_type=ACCESS_TOKEN_TYPE,
        requested_token_type=None,
        scope=frozenset({"chat:read"}),
        resource=resource,
    )


class TestResolveExchangeTarget:
    def test_no_resource_inherits_subject_audience(self) -> None:
        aud = resolve_exchange_target(
            request=_target_request(None),
            actor=_target_actor(frozenset({"https://api.example/"})),
            subject=_target_subject(audience="https://issuer.example/"),
        )
        assert aud == "https://issuer.example/"

    def test_registered_resource_becomes_the_audience(self) -> None:
        aud = resolve_exchange_target(
            request=_target_request("https://api.example/v1"),
            actor=_target_actor(frozenset({"https://api.example/v1"})),
            subject=_target_subject(),
        )
        assert aud == "https://api.example/v1"

    def test_unregistered_resource_is_refused(self) -> None:
        with pytest.raises(InvalidTargetError):
            resolve_exchange_target(
                request=_target_request("https://other.example/"),
                actor=_target_actor(frozenset({"https://api.example/v1"})),
                subject=_target_subject(),
            )

    def test_actor_with_no_registered_resources_is_refused(self) -> None:
        """Fail-closed: a resource request from a client registered for no
        targets must not silently mint a token aimed anywhere."""
        with pytest.raises(InvalidTargetError):
            resolve_exchange_target(
                request=_target_request("https://api.example/v1"),
                actor=_target_actor(frozenset()),
                subject=_target_subject(),
            )

    @pytest.mark.parametrize(
        "bad",
        [
            "not-a-uri",
            "/relative/path",
            "https://api.example/v1#frag",  # RFC 8707: fragment forbidden
            "ftp://api.example/",
            "https://",  # no host
        ],
    )
    def test_malformed_resource_is_refused(self, bad: str) -> None:
        with pytest.raises(InvalidTargetError):
            resolve_exchange_target(
                request=_target_request(bad),
                actor=_target_actor(frozenset({bad})),
                subject=_target_subject(),
            )
