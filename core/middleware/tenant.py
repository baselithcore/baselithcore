"""
Tenant Middleware.

Derives the tenant (and user) context from the request's *credential* and binds
it to contextvars + structlog for the whole downstream stack.

Identity is resolved through the shared per-request memo in
:mod:`core.middleware._auth_memo` — the same verification
:class:`~core.middleware.quota.QuotaMiddleware` and the route's auth dependency
use, so the token is verified once and every layer agrees on who is calling.
"""

from starlette.types import ASGIApp, Receive, Scope, Send

from core.auth import AuthUser
from core.context import (
    ReservedTenantError,
    bind_principal_tenant,
    reset_tenant_context,
    reset_user_context,
    set_user_context,
)
from core.middleware._auth_memo import resolve_user

try:
    import structlog  # type: ignore
    from structlog.contextvars import bind_contextvars  # type: ignore
except ImportError:
    structlog = None  # type: ignore
    bind_contextvars = None  # type: ignore


class TenantMiddleware:
    """Pure ASGI middleware that derives tenant context from the auth user.

    Binds the tenant id to a contextvar plus structlog for the duration of the
    request. Skips lifespan and websocket scopes.

    Two sources, in this order:

    1. An ``AuthUser`` an **outer** layer already attached to the connection
       (``scope['state']['user']`` or ``scope['user']``). It wins because a
       custom auth middleware mounted in front of this one — or a test harness —
       has by definition already decided who the caller is, and re-deriving it
       from the raw header would override that decision.
    2. Otherwise the credential on the request, verified through the shared
       memo.

    What is *not* a source is the route's auth dependency: it writes
    ``request.state.user`` long after every middleware has run, so reading that
    key here (which is all this middleware used to do) left the tenant
    contextvar at ``"default"`` for every authenticated request, and every inner
    layer — idempotency keys, plugin context, storage — inherited the wrong
    tenant.

    **Reserved identities are refused here.** Both sources above ultimately
    carry a claim — a JWT ``tenant_id``, an API-key record, or whatever an outer
    auth middleware decided — and ``system`` is the identity the framework binds
    for maintenance work, which migration ``010_system_tenant_rls_exemption``
    grants visibility of every tenant's rows. A request that arrived claiming it
    would execute with total access, so it is rejected at the boundary rather
    than trusted: no token, however issued, gets a system-scoped session. See
    :data:`core.context.RESERVED_TENANT_IDS`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _preset_user(scope: Scope) -> AuthUser | None:
        """An ``AuthUser`` an outer layer already attached to this request."""
        state = scope.get("state") or {}
        user = (
            state.get("user")
            if isinstance(state, dict)
            else getattr(state, "user", None)
        )
        if isinstance(user, AuthUser):
            return user
        scope_user = scope.get("user")
        return scope_user if isinstance(scope_user, AuthUser) else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        user = self._preset_user(scope)
        if user is None:
            user = await resolve_user(scope)

        tenant_id = user.tenant_id if isinstance(user, AuthUser) else "default"
        user_id = user.user_id if isinstance(user, AuthUser) else None

        # Through ``bind_principal_tenant``, never the plain setter: this value
        # came from a credential, and the helper is what refuses a reserved
        # identity. Doing the check here instead would protect this site and no
        # other — the mistake round 2b made.
        try:
            token = bind_principal_tenant(tenant_id)
        except ReservedTenantError:
            await self._refuse_reserved_tenant(
                scope, receive, send, tenant_id=tenant_id, user_id=user_id
            )
            return
        user_token = set_user_context(user_id) if user_id else None
        if structlog and bind_contextvars is not None:
            bind_contextvars(tenant_id=tenant_id)

        try:
            await self.app(scope, receive, send)
        finally:
            reset_tenant_context(token)
            if user_token is not None:
                reset_user_context(user_token)

    @staticmethod
    async def _refuse_reserved_tenant(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        tenant_id: str,
        user_id: str | None,
    ) -> None:
        """Reject a request whose principal claims a reserved tenant.

        403 rather than 401: the credential may well be valid — what is refused
        is the identity it asserts. The request never reaches the application,
        so nothing downstream can observe the reserved tenant even briefly.

        Both imports are local: ``core.api.errors`` imports the FastAPI surface
        that mounts this middleware, and the audit logger is only needed on a
        path that should never be taken.
        """
        from core.api.errors import problem_response
        from core.middleware._security_metrics import SECURITY_EVENTS
        from core.observability.audit import AuditEventType, audit_emit

        # Same counter and label the ``enforce_auth`` half emits, so
        # ``security_events_total{reason="reserved_tenant"}`` covers both doors.
        # Without it the HTTP half of the guard was invisible on the dashboard
        # that exists to show exactly this.
        SECURITY_EVENTS.labels(reason="reserved_tenant").inc()
        audit_emit(
            AuditEventType.AUTH_FAILED,
            user_id=user_id,
            tenant_id=tenant_id,
            resource=f"tenant:{tenant_id}",
            action="reserved_tenant_rejected",
            success=False,
            details={
                "reason": "principal claimed a framework-reserved tenant id",
                "path": scope.get("path"),
                "method": scope.get("method"),
            },
        )
        response = problem_response(
            status_code=403,
            code="reserved_tenant",
            detail=(
                f"'{tenant_id}' is a reserved tenant identifier and cannot be "
                "used by a request. It belongs to the framework's own "
                "maintenance context."
            ),
            error_type="authorization_error",
        )
        await response(scope, receive, send)
