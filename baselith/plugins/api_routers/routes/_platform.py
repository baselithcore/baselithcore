"""The platform-credential rule for deployment-wide admin surfaces.

Some stores hold one set of records for the whole deployment: the retention
sweep deletes expired rows of every tenant, and the compliance registers (AI
systems, FRIA, RoPA, post-market plans, the audit chain) have no tenant column
at all. A scope on a credential bound to *another* tenant must not reach them:
that holder would read and change the operator's own records. Such surfaces are
limited to credentials of the platform tenant.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from baselith.core.auth.types import AuthUser

#: The tenant platform-level credentials are bound to (the unauthenticated and
#: single-tenant default). Deployment-wide operations are limited to it.
PLATFORM_TENANT = "default"


def require_platform_credential(user: AuthUser, what: str) -> None:
    """Refuse a credential bound to a tenant other than the platform's.

    Args:
        user: The authenticated caller, scopes already enforced.
        what: The surface, as the refusal names it ("the retention sweep").

    Raises:
        HTTPException: 403 when ``user`` belongs to another tenant.
    """
    if user.tenant_id != PLATFORM_TENANT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{what} spans every tenant; "
            "it is limited to platform-level credentials",
        )


__all__ = ["PLATFORM_TENANT", "require_platform_credential"]
