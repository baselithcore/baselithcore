"""Grant-level validation rules for the authorization server.

Three rules carry most of the security weight here and are worth stating
plainly:

* **Redirect URIs match exactly.** Prefix or wildcard matching is how open
  redirectors become token exfiltration. The single exemption is loopback
  redirection for native clients (RFC 8252 §7.3), where the port is chosen at
  runtime and therefore cannot be registered.
* **Scope is an intersection, never a union.** A client can only narrow what
  the subject already holds. This is the property that makes delegating a token
  to an agent safe. The subject's side of that intersection is evaluated with
  the wildcard grammar of :mod:`core.auth.scopes` — an admin holding ``"*"``
  satisfies every scope, a service identity holding ``"chat:*"`` satisfies
  every ``chat:`` action — because those are grants, not literal capability
  strings. The client's side is *not* expanded: a registration is an explicit
  list, and the granted set is always bounded by it.
* **A client may only use grants it is registered for.** Registration is the
  only place a grant is enabled.
"""

from __future__ import annotations

from urllib.parse import urlparse

from core.auth.oauth._errors import (
    InvalidRequestError,
    InvalidScopeError,
    UnauthorizedClientError,
)
from core.auth.oauth._models import AuthorizationRequest, GrantType, OAuthClient
from core.auth.oauth._pkce import S256
from core.auth.scopes import scope_satisfied


def _is_loopback(parsed: object) -> bool:
    """Report whether a parsed URI's host is a loopback address.

    Args:
        parsed: The result of ``urlparse`` on a redirect URI.

    Returns:
        True when the host is ``127.0.0.1`` or ``::1``.
    """
    host = getattr(parsed, "hostname", None)
    return host in {"127.0.0.1", "::1"}


def validate_redirect_uri(client: OAuthClient, redirect_uri: str) -> str:
    """Match a requested redirect URI against the client's registrations.

    Args:
        client: The registered client.
        redirect_uri: The URI from the authorization request.

    Returns:
        The redirect URI to use, unchanged.

    Raises:
        InvalidRequestError: If no registration matches.
    """
    if redirect_uri in client.redirect_uris:
        return redirect_uri

    candidate = urlparse(redirect_uri)
    if _is_loopback(candidate):
        for registered in client.redirect_uris:
            known = urlparse(registered)
            if (
                _is_loopback(known)
                and known.scheme == candidate.scheme
                and known.hostname == candidate.hostname
                and known.path == candidate.path
                and known.query == candidate.query
            ):
                return redirect_uri

    raise InvalidRequestError("redirect_uri does not match a registered value")


def resolve_scope(
    *,
    requested: frozenset[str],
    client_allowed: frozenset[str],
    subject_scopes: frozenset[str],
    allow_empty: bool = True,
) -> frozenset[str]:
    """Intersect requested, client-permitted and subject-held scopes.

    The candidate set is every scope the client is registered for that the
    subject satisfies. "Satisfies" is :func:`core.auth.scopes.scope_satisfied`,
    not literal membership: a subject holding ``"*"`` (admin) or ``"chat:*"``
    (a service identity) holds those scopes by grant, and a plain set
    intersection would silently read those wildcards as opaque strings and
    lock the identity out of every OAuth flow.

    The expansion is deliberately one-sided. ``client_allowed`` is never
    expanded — a registration is an explicit list, and the result stays a
    subset of it — so this narrows nothing and grants nothing the client was
    not already registered for.

    Args:
        requested: Scopes named in the request. Empty means "everything the
            client is registered for that the subject satisfies".
        client_allowed: Scopes the client is registered to request. Taken
            literally; a wildcard here is matched as a string, not expanded.
        subject_scopes: Scopes the authenticated subject actually holds,
            wildcards included.
        allow_empty: When False, an empty result raises rather than issuing a
            zero-privilege token.

    Returns:
        The granted scope set. Always a subset of ``client_allowed``.

    Raises:
        InvalidScopeError: If the intersection is empty and ``allow_empty`` is
            False.
    """
    available = frozenset(
        s for s in client_allowed if scope_satisfied(subject_scopes, s)
    )
    granted = available if not requested else requested & available
    if not granted and not allow_empty:
        raise InvalidScopeError(
            "none of the requested scopes are available to this client and user"
        )
    return frozenset(granted)


def assert_grant_allowed(client: OAuthClient, grant: GrantType) -> None:
    """Raise unless the client is registered for ``grant``.

    Args:
        client: The registered client.
        grant: The grant type the caller is about to use.

    Raises:
        UnauthorizedClientError: If the grant is not registered.
    """
    if grant not in client.grant_types:
        raise UnauthorizedClientError(
            f"client is not registered for grant type {grant.value!r}"
        )


def validate_authorization_request(
    client: OAuthClient,
    *,
    redirect_uri: str,
    scope: frozenset[str],
    state: str,
    code_challenge: str,
    code_challenge_method: str,
    resource: str | None,
    known_resources: frozenset[str],
) -> AuthorizationRequest:
    """Validate a ``GET /oauth/authorize`` request.

    Scope is deliberately *not* resolved here: the subject's own scopes are a
    plugin concern, so the caller narrows the set once it knows who is logged
    in.

    Args:
        client: The registered client making the request.
        redirect_uri: The URI to redirect back to.
        scope: The scopes requested.
        state: The client's opaque CSRF-protection value.
        code_challenge: The PKCE code challenge (mandatory).
        code_challenge_method: The PKCE method; only ``S256`` is accepted.
        resource: The RFC 8707 resource indicator, if any.
        known_resources: Resource identifiers this deployment recognizes.

    Returns:
        The validated request, awaiting user consent.

    Raises:
        OAuthError: Any RFC 6749 §5.2 failure.
    """
    assert_grant_allowed(client, GrantType.AUTHORIZATION_CODE)
    validated_uri = validate_redirect_uri(client, redirect_uri)

    if not state:
        raise InvalidRequestError("state is required")
    if not code_challenge:
        raise InvalidRequestError("code_challenge is required (PKCE is mandatory)")
    if code_challenge_method != S256:
        raise InvalidRequestError("code_challenge_method must be S256")
    if resource is not None and resource not in known_resources:
        raise InvalidRequestError(f"unknown resource {resource!r}")

    unknown = scope - client.allowed_scopes
    if unknown:
        raise InvalidScopeError(
            "client may not request scope(s): " + " ".join(sorted(unknown))
        )

    return AuthorizationRequest(
        client_id=client.client_id,
        redirect_uri=validated_uri,
        scope=scope,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        resource=resource,
    )
