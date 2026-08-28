"""Runtime environment resolution.

Single source of truth for "are we in production?", which gates every
fail-closed control in the framework: plugin signature enforcement, unsigned
A2A rejection, the A2A SSRF internal-host deny, admin lockout on Redis loss,
the JWT issuer/audience startup check and anonymous ``/docs`` exposure.

Deliberately **stdlib-only**. ``core.plugins.integrity`` and
``core.a2a.security`` each carried their own copy of this logic to avoid
dragging pydantic in through ``core.config``'s package init; both now import
from here instead, so the three copies can no longer drift.

Why normalization matters
-------------------------
Matching the literal string ``"production"`` meant ``APP_ENV=prod`` — the most
common spelling in the wild — silently disabled every control listed above,
*and* counted as "environment declared", which also defeated the
"smells like prod" fallback in the API factory. Aliases are therefore folded
onto the canonical name, and anything unrecognised fails closed: an
environment we cannot classify is treated as production rather than assumed
safe. Declaring a known non-production name is the way out.
"""

from __future__ import annotations

import os

PRODUCTION = "production"

# Spellings that mean production. Matched after lowercasing and stripping.
_PRODUCTION_ALIASES: frozenset[str] = frozenset({"production", "prod", "prd", "live"})

# Environments known not to be production. Anything outside both sets is
# unrecognised and therefore treated as production (fail closed).
_NON_PRODUCTION_ALIASES: frozenset[str] = frozenset(
    {
        "development",
        "develop",
        "dev",
        "local",
        "localhost",
        "test",
        "testing",
        "tests",
        "ci",
        "staging",
        "stage",
        "stg",
        "qa",
        "uat",
        "sandbox",
        "demo",
        "preview",
        "preprod",
        "pre-production",
        "pre-prod",
        "nonprod",
        "non-production",
        "non-prod",
    }
)


def get_runtime_environment(default: str = "development") -> str:
    """Return the effective runtime environment name, normalized.

    ``APP_ENV`` wins over ``ENVIRONMENT``. Production aliases (``prod``,
    ``prd``, ``live``) are folded onto ``"production"``; every other value is
    returned lowercased and stripped as declared.
    """
    value = os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or default
    normalized = value.strip().lower()
    if normalized in _PRODUCTION_ALIASES:
        return PRODUCTION
    return normalized


def is_known_environment(name: str) -> bool:
    """Whether ``name`` is an environment this framework can classify."""
    normalized = name.strip().lower()
    return normalized in _PRODUCTION_ALIASES or normalized in _NON_PRODUCTION_ALIASES


def is_production_env() -> bool:
    """Return True when the application must apply production hardening.

    True for every production alias, and also for any environment name we do
    not recognise: a deployment we cannot classify gets the hardened posture
    rather than the permissive one. Set ``APP_ENV`` to a known non-production
    name (``development``, ``staging``, ``test``, ...) to opt out.
    """
    environment = get_runtime_environment()
    if environment == PRODUCTION:
        return True
    return not is_known_environment(environment)


__all__ = [
    "PRODUCTION",
    "get_runtime_environment",
    "is_known_environment",
    "is_production_env",
]
