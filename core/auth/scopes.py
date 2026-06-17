"""
Fine-grained authorization scopes (capability-based access control).

A *scope* is a ``"resource:action"`` capability string (e.g. ``"chat:write"``).
On top of the coarse :class:`~core.auth.types.AuthRole` model this adds
capability-level authorization so API keys and tokens can be granted exactly
the access they need (least privilege) instead of a whole role.

Two wildcards are supported:

* ``"*"`` — grants every scope (superuser / full access).
* ``"resource:*"`` — grants every action on a single resource.

The authorization an identity actually carries is the **union** of:

* scopes derived from its roles (:data:`ROLE_SCOPES`) — this keeps the
  role-only model working unchanged (zero regression), and
* any explicit scopes attached to the identity (a scoped API key, or a JWT
  ``"scopes"`` claim).

This module is deliberately domain-agnostic: it defines the grammar and the
default role→scope mapping, not which endpoint requires which scope (that is a
routing/middleware concern). It therefore belongs in the Sacred Core.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.auth.types import AuthRole

# Wildcard that grants every scope. Hold it on an admin/superuser identity.
SUPERUSER_SCOPE = "*"

# Separator between the resource and the action in a scope string.
_SCOPE_SEP = ":"


# === Canonical scope catalog ===
# Grouped by resource. "manage" is the privileged action (create/rotate/delete);
# "read"/"write" are the data-plane actions. New resources should follow the
# same resource:action shape so wildcard expansion stays predictable.
SCOPE_CHAT_READ = "chat:read"
SCOPE_CHAT_WRITE = "chat:write"
SCOPE_MEMORY_READ = "memory:read"
SCOPE_MEMORY_WRITE = "memory:write"
SCOPE_FEEDBACK_WRITE = "feedback:write"
SCOPE_METRICS_READ = "metrics:read"
SCOPE_WEBHOOKS_READ = "webhooks:read"
SCOPE_WEBHOOKS_WRITE = "webhooks:write"
SCOPE_KEYS_MANAGE = "keys:manage"
SCOPE_FLAGS_MANAGE = "flags:manage"
SCOPE_DLQ_MANAGE = "dlq:manage"
SCOPE_TENANTS_MANAGE = "tenants:manage"
SCOPE_PLUGINS_MANAGE = "plugins:manage"

# Every concrete scope the framework knows about. Used for validation and for
# discovery surfaces (admin console, key-issuance UI). Wildcards are not
# included here — they are matchers, not grantable catalog entries on their own
# (though they are perfectly valid to grant).
KNOWN_SCOPES: frozenset[str] = frozenset(
    {
        SCOPE_CHAT_READ,
        SCOPE_CHAT_WRITE,
        SCOPE_MEMORY_READ,
        SCOPE_MEMORY_WRITE,
        SCOPE_FEEDBACK_WRITE,
        SCOPE_METRICS_READ,
        SCOPE_WEBHOOKS_READ,
        SCOPE_WEBHOOKS_WRITE,
        SCOPE_KEYS_MANAGE,
        SCOPE_FLAGS_MANAGE,
        SCOPE_DLQ_MANAGE,
        SCOPE_TENANTS_MANAGE,
        SCOPE_PLUGINS_MANAGE,
    }
)


# === Default role → scope mapping ===
# Backward compatibility: an identity that only carries roles (the pre-scopes
# model) keeps exactly the access those roles imply, expressed as scopes here.
# ADMIN/SERVICE are intentionally broad; GUEST/JOB are least-privilege.
ROLE_SCOPES: dict[AuthRole, frozenset[str]] = {
    # Full access — the superuser wildcard subsumes every present and future scope.
    AuthRole.ADMIN: frozenset({SUPERUSER_SCOPE}),
    # Service-to-service: all data-plane resources, no control-plane (keys, flags,
    # tenants, plugins, dlq) which stay admin-only.
    AuthRole.SERVICE: frozenset(
        {"chat:*", "memory:*", "feedback:*", "webhooks:*", SCOPE_METRICS_READ}
    ),
    # Interactive end user.
    AuthRole.USER: frozenset(
        {
            SCOPE_CHAT_READ,
            SCOPE_CHAT_WRITE,
            SCOPE_MEMORY_READ,
            SCOPE_MEMORY_WRITE,
            SCOPE_FEEDBACK_WRITE,
            SCOPE_METRICS_READ,
        }
    ),
    # Automated job/scheduler: produce chat, read memory/metrics, no writes to
    # memory and no feedback.
    AuthRole.JOB: frozenset(
        {SCOPE_CHAT_READ, SCOPE_CHAT_WRITE, SCOPE_MEMORY_READ, SCOPE_METRICS_READ}
    ),
    # Read-only dashboard viewer.
    AuthRole.GUEST: frozenset({SCOPE_CHAT_READ, SCOPE_METRICS_READ}),
    # No capabilities.
    AuthRole.ANONYMOUS: frozenset(),
}


def scope_satisfied(granted: Iterable[str], required: str) -> bool:
    """Return whether ``required`` is covered by the ``granted`` scopes.

    Matching rules, in order:

    * exact match (``required`` literally present), or
    * the superuser wildcard ``"*"`` is granted, or
    * the resource wildcard ``"<resource>:*"`` is granted.

    Args:
        granted: The scopes an identity holds.
        required: The single scope being checked (``"resource:action"``).

    Returns:
        ``True`` if the requirement is satisfied.
    """
    granted_set = granted if isinstance(granted, (set, frozenset)) else set(granted)
    if required in granted_set or SUPERUSER_SCOPE in granted_set:
        return True
    resource, sep, _ = required.partition(_SCOPE_SEP)
    if sep and f"{resource}{_SCOPE_SEP}*" in granted_set:
        return True
    return False


def scopes_satisfied(
    granted: Iterable[str],
    required: Iterable[str],
    *,
    require_all: bool = True,
) -> bool:
    """Check a set of required scopes against the granted scopes.

    Args:
        granted: The scopes an identity holds.
        required: The scopes being demanded. An empty requirement is always
            satisfied.
        require_all: When ``True`` (default) every required scope must be
            satisfied; when ``False`` any single match suffices.

    Returns:
        ``True`` if the requirement is satisfied.
    """
    granted_set = granted if isinstance(granted, (set, frozenset)) else set(granted)
    required_list = list(required)
    if not required_list:
        return True
    checker = (scope_satisfied(granted_set, r) for r in required_list)
    return all(checker) if require_all else any(checker)


def expand_roles(roles: Iterable[AuthRole]) -> frozenset[str]:
    """Expand a set of roles into the union of their default scopes."""
    out: set[str] = set()
    for role in roles:
        out |= ROLE_SCOPES.get(role, frozenset())
    return frozenset(out)


def effective_scopes(
    roles: Iterable[AuthRole],
    explicit: Iterable[str] | None = None,
) -> frozenset[str]:
    """Compute the full capability set for an identity.

    The result is the union of role-derived scopes and any explicit grants. An
    explicit grant can only *add* capability, never remove what a role implies.

    Args:
        roles: The identity's roles.
        explicit: Scopes attached directly to the identity (scoped key / JWT
            claim). May be ``None``.

    Returns:
        The effective scope set.
    """
    return expand_roles(roles) | frozenset(explicit or ())


def normalize_scope(raw: str) -> str:
    """Normalize a raw scope string (trim + lowercase the resource/action)."""
    return raw.strip().lower()


def parse_scope_list(raw: str, *, sep: str = "|") -> set[str]:
    """Parse a ``sep``-delimited scope string into a normalized set.

    Empty entries are dropped. Used by config loaders for scoped API keys.
    """
    return {normalize_scope(s) for s in raw.split(sep) if normalize_scope(s)}
