"""Redirect-URI matching, scope intersection and grant gating."""

from __future__ import annotations

import pytest

from core.auth.oauth import (
    ClientType,
    GrantType,
    InvalidRequestError,
    InvalidScopeError,
    OAuthClient,
    UnauthorizedClientError,
    assert_grant_allowed,
    resolve_scope,
    validate_redirect_uri,
)


def _client(**overrides: object) -> OAuthClient:
    base = dict(
        client_id="cli-1",
        client_type=ClientType.PUBLIC,
        redirect_uris=("https://app.example/cb", "http://127.0.0.1:0/cb"),
        grant_types=frozenset({GrantType.AUTHORIZATION_CODE}),
        allowed_scopes=frozenset({"chat:read", "chat:write"}),
    )
    base.update(overrides)
    return OAuthClient(**base)  # type: ignore[arg-type]


def test_exact_redirect_uri_matches() -> None:
    assert validate_redirect_uri(_client(), "https://app.example/cb") == (
        "https://app.example/cb"
    )


def test_prefix_of_registered_uri_is_rejected() -> None:
    with pytest.raises(InvalidRequestError):
        validate_redirect_uri(_client(), "https://app.example/cb/evil")


def test_different_query_is_rejected() -> None:
    with pytest.raises(InvalidRequestError):
        validate_redirect_uri(_client(), "https://app.example/cb?x=1")


def test_loopback_port_is_ignored() -> None:
    # RFC 8252: a native client cannot reserve a port in advance.
    assert validate_redirect_uri(_client(), "http://127.0.0.1:54321/cb") == (
        "http://127.0.0.1:54321/cb"
    )


def test_loopback_path_still_must_match() -> None:
    with pytest.raises(InvalidRequestError):
        validate_redirect_uri(_client(), "http://127.0.0.1:54321/other")


def test_non_loopback_host_gets_no_port_exemption() -> None:
    with pytest.raises(InvalidRequestError):
        validate_redirect_uri(_client(), "https://app.example:8443/cb")


def test_scope_is_the_three_way_intersection() -> None:
    granted = resolve_scope(
        requested=frozenset({"chat:read", "chat:write", "keys:manage"}),
        client_allowed=frozenset({"chat:read", "chat:write"}),
        subject_scopes=frozenset({"chat:read", "keys:manage"}),
    )
    assert granted == frozenset({"chat:read"})


def test_scope_cannot_exceed_subject_privileges() -> None:
    # The client is allowed to ask for it; the user does not have it.
    granted = resolve_scope(
        requested=frozenset({"keys:manage"}),
        client_allowed=frozenset({"keys:manage", "chat:read"}),
        subject_scopes=frozenset({"chat:read"}),
    )
    assert granted == frozenset()  # empty -> the caller raises; see next test


def test_empty_intersection_is_rejected_when_scope_requested() -> None:
    with pytest.raises(InvalidScopeError):
        resolve_scope(
            requested=frozenset({"keys:manage"}),
            client_allowed=frozenset({"keys:manage"}),
            subject_scopes=frozenset({"chat:read"}),
            allow_empty=False,
        )


def test_superuser_wildcard_subject_satisfies_every_client_scope() -> None:
    # An AuthRole.ADMIN holds exactly {"*"}. A literal set intersection makes
    # that the empty set, which allow_empty=False turns into invalid_scope —
    # i.e. no admin could complete any OAuth flow at all.
    granted = resolve_scope(
        requested=frozenset({"chat:read"}),
        client_allowed=frozenset({"chat:read", "chat:write"}),
        subject_scopes=frozenset({"*"}),
    )
    assert granted == frozenset({"chat:read"})


def test_superuser_wildcard_with_no_request_grants_the_whole_registration() -> None:
    granted = resolve_scope(
        requested=frozenset(),
        client_allowed=frozenset({"chat:read", "keys:manage"}),
        subject_scopes=frozenset({"*"}),
    )
    assert granted == frozenset({"chat:read", "keys:manage"})


def test_prefix_wildcard_subject_covers_only_its_own_resource() -> None:
    # "chat:*" is not "*": it expands across the chat resource and stops there.
    granted = resolve_scope(
        requested=frozenset(),
        client_allowed=frozenset({"chat:read", "chat:write", "keys:manage"}),
        subject_scopes=frozenset({"chat:*"}),
    )
    assert granted == frozenset({"chat:read", "chat:write"})


def test_wildcard_subject_never_widens_beyond_the_client_registration() -> None:
    # The client's registration is still the ceiling, and the literal "*" is
    # never itself handed out as a granted scope.
    granted = resolve_scope(
        requested=frozenset(),
        client_allowed=frozenset({"chat:read"}),
        subject_scopes=frozenset({"*"}),
    )
    assert granted == frozenset({"chat:read"})
    assert "*" not in granted


def test_client_side_wildcard_is_not_expanded() -> None:
    # A registration is an explicit list, never a matcher. A client registered
    # for "chat:*" does not thereby become registered for "chat:read", so a
    # subject holding only "chat:read" gets nothing from it.
    granted = resolve_scope(
        requested=frozenset(),
        client_allowed=frozenset({"chat:*"}),
        subject_scopes=frozenset({"chat:read"}),
    )
    assert granted == frozenset()


def test_client_side_wildcard_is_matched_literally_not_as_a_grant() -> None:
    # The only way "chat:*" survives is a subject that genuinely holds a grant
    # covering it — and then it is carried through as the exact string the
    # client registered, not exploded into concrete scopes.
    granted = resolve_scope(
        requested=frozenset(),
        client_allowed=frozenset({"chat:*"}),
        subject_scopes=frozenset({"chat:*"}),
    )
    assert granted == frozenset({"chat:*"})


def test_wildcard_subject_still_rejected_when_client_allows_nothing() -> None:
    with pytest.raises(InvalidScopeError):
        resolve_scope(
            requested=frozenset(),
            client_allowed=frozenset(),
            subject_scopes=frozenset({"*"}),
            allow_empty=False,
        )


def test_grant_not_registered_for_client_is_refused() -> None:
    with pytest.raises(UnauthorizedClientError):
        assert_grant_allowed(_client(), GrantType.CLIENT_CREDENTIALS)
