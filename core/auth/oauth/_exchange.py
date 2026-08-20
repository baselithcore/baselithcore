"""RFC 8693 token exchange rules.

Delegation, not impersonation: the exchanged token keeps the original user as
``sub`` and names the agent in ``act``, so every downstream authorization check
still runs as the user — with less scope — while the audit trail records who
actually made the call.

Three properties carry the security weight and are asserted directly in tests:

* **One hop.** A subject token that already carries ``act`` is refused, so a
  token this server already delegated cannot be delegated again — chain
  semantics are removed entirely. (An admin impersonation token also carries
  ``act``, but it is never refused *here*: it is an HS256 session token whose
  ``kid`` is absent from the OAuth key store, so it fails subject-token
  verification as unverifiable and is not a valid subject token at all.)
* **Scope only narrows.** The granted set is the intersection of what was
  asked for, what the subject token holds, and what the agent is registered
  for — never a union, and never wider than any operand.
* **The actor is the authenticated client.** There is no ``actor_token``: a
  second representation of the same fact could disagree with the first.
"""

from __future__ import annotations

from urllib.parse import urlparse

from core.auth.oauth._errors import (
    InvalidClientError,
    InvalidGrantError,
    InvalidRequestError,
    InvalidScopeError,
    InvalidTargetError,
    UnauthorizedClientError,
)
from core.auth.oauth._models import (
    ACCESS_TOKEN_TYPE,
    ClientType,
    GrantType,
    OAuthClient,
    SubjectTokenContext,
    TokenExchangeRequest,
)


def validate_exchange_request(
    actor: OAuthClient, request: TokenExchangeRequest
) -> None:
    """Check the actor client and the request shape (rules 1–3 and scope presence).

    Args:
        actor: The authenticated client asking to act for a user.
        request: The parsed exchange request.

    Raises:
        InvalidClientError: If the actor is a public client. A public client
            holds no secret, so anyone replaying its id could act for any user.
        UnauthorizedClientError: If the actor is not registered for the
            token-exchange grant.
        InvalidRequestError: If a token type is unsupported, or ``scope`` is
            absent — narrowing is the point of this grant, and inheriting
            everything silently is not a request anyone should make by accident.
    """
    if actor.client_type is not ClientType.CONFIDENTIAL:
        raise InvalidClientError("token exchange requires a confidential client")
    if GrantType.TOKEN_EXCHANGE not in actor.grant_types:
        raise UnauthorizedClientError(
            "client is not registered for grant type "
            f"{GrantType.TOKEN_EXCHANGE.value!r}"
        )
    if request.subject_token_type != ACCESS_TOKEN_TYPE:
        raise InvalidRequestError(
            f"unsupported subject_token_type {request.subject_token_type!r}"
        )
    if request.requested_token_type not in (None, ACCESS_TOKEN_TYPE):
        raise InvalidRequestError(
            f"unsupported requested_token_type {request.requested_token_type!r}"
        )
    if not request.scope:
        raise InvalidRequestError("scope is required for a token exchange")


def validate_delegation(
    subject: SubjectTokenContext,
    *,
    actor: OAuthClient,
    subject_client_actors: frozenset[str],
) -> None:
    """Check that this actor may act for this subject token (rules 5–8).

    The allowlist is checked before the tenant comparison on purpose: an actor
    outside the allowlist is refused before the tenants are ever compared, so
    the difference between the two error codes cannot be used to learn whether
    a client id exists in another tenant.

    Rule 8 follows the same convention ``routes/_authorize.py`` already
    established for the authorization endpoint: a tenant-scoped actor may only
    act for that tenant's subjects, but a tenantless actor is unscoped by
    design and serves any subject. Strict equality (the ``authorize`` route's
    earlier approach) refuses this unconditionally, because a logged-in user's
    token always carries a concrete tenant (the deployment's pinned tenant, or
    their personal ``tenant_id == user_id`` fallback) while a client with no
    registered tenant is ``None`` — so equality could never hold and every
    real delegation would be refused.

    Args:
        subject: The verified subject token's context.
        actor: The authenticated agent client.
        subject_client_actors: ``allowed_actors`` of the client the subject
            token was issued to.

    Raises:
        InvalidGrantError: If the subject token is already delegated, has no
            user subject, or the actor is scoped to a different tenant than
            the subject token.
        UnauthorizedClientError: If the actor is not in the allowlist.
    """
    if subject.has_actor:
        raise InvalidGrantError("an already delegated token cannot be exchanged again")
    if subject.subject == subject.client_id:
        raise InvalidGrantError("the subject token has no user to delegate for")
    if actor.client_id not in subject_client_actors:
        raise UnauthorizedClientError(
            "this client is not an authorized actor for the subject token"
        )
    if actor.tenant_id is not None and actor.tenant_id != subject.tenant_id:
        raise InvalidGrantError("a delegation may not cross tenants")


def resolve_exchange_scope(
    *,
    requested: frozenset[str],
    subject_scope: frozenset[str],
    actor_allowed: frozenset[str],
) -> frozenset[str]:
    """Intersect requested, subject-held and actor-registered scopes (rule 9).

    Unlike :func:`core.auth.oauth.resolve_scope`, no wildcard expansion happens
    here: both the subject token's ``scope`` claim and the agent's registration
    are already concrete scope lists, not grants.

    Args:
        requested: Scopes named in the exchange request.
        subject_scope: Scopes the subject token actually carries.
        actor_allowed: Scopes the agent client is registered for.

    Returns:
        The granted set — a subset of all three operands.

    Raises:
        InvalidScopeError: If the intersection is empty. A zero-privilege token
            would look like a success while being useless.
    """
    granted = requested & subject_scope & actor_allowed
    if not granted:
        raise InvalidScopeError(
            "none of the requested scopes are held by the subject token and "
            "available to this client"
        )
    return frozenset(granted)


def resolve_exchange_target(
    *,
    request: TokenExchangeRequest,
    actor: OAuthClient,
    subject: SubjectTokenContext,
) -> str | None:
    """Validate the RFC 8693 ``resource`` target and pick the token's audience
    (rule 10).

    Audience restriction is RFC 8693's mechanism for stopping a delegated
    token being replayed at a different resource server — parsing ``resource``
    without enforcing it leaves that mechanism absent.

    * No ``resource`` requested → the delegated token inherits the subject
      token's audience unchanged (it never widens where the token is valid).
    * ``resource`` requested → it must be an RFC 8707 resource indicator (an
      absolute ``http(s)`` URI with a host and **no fragment**) AND appear in
      the actor client's registered ``allowed_resources``. A client registered
      for no targets gets every ``resource`` request refused — fail-closed,
      never a token silently aimed anywhere the client asks.

    Args:
        request: The parsed exchange request.
        actor: The authenticated agent client.
        subject: The verified subject token's context.

    Returns:
        The audience to mint into the delegated token (``None`` when the
        subject token carried none and no resource was requested).

    Raises:
        InvalidTargetError: If ``resource`` is malformed or the actor is not
            registered for it.
    """
    resource = request.resource
    if resource is None:
        return subject.audience

    parsed = urlparse(resource)
    if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.fragment:
        raise InvalidTargetError(
            "resource must be an absolute http(s) URI without a fragment"
        )
    if resource not in actor.allowed_resources:
        raise InvalidTargetError(
            "this client is not registered for the requested resource"
        )
    return resource


def build_actor_claim(actor_client_id: str) -> dict[str, str]:
    """Build the RFC 8693 ``act`` claim for an agent client.

    Both members are present deliberately. Admin impersonation
    (``plugins.auth.impersonation``) mints ``act.sub`` holding a *user* id, so
    ``act.client_id`` is what lets a consumer tell an agent delegation from an
    impersonation without guessing.

    Args:
        actor_client_id: The agent's client id.

    Returns:
        The ``act`` claim value.
    """
    return {"sub": actor_client_id, "client_id": actor_client_id}


def build_may_act_claim(allowed_actors: frozenset[str]) -> dict[str, list[str]] | None:
    """Build the ``may_act`` claim advertising who may exchange this token.

    RFC 8693 §4.4 defines ``may_act`` as identifying *a* permitted party; a list
    under ``client_id`` extends that shape, which is documented in
    ``reference/security.md``. It is a hint for an offline verifier only — the
    exchange always re-reads the allowlist from the database, so removing an
    actor takes effect immediately rather than when outstanding tokens expire.

    Args:
        allowed_actors: The issuing client's registered actors.

    Returns:
        The claim value, or ``None`` when there are no actors and the claim
        should be omitted entirely.
    """
    if not allowed_actors:
        return None
    return {"client_id": sorted(allowed_actors)}
