"""
Tests for Tenant Middleware.
"""

import pytest

from core.auth import AuthRole, AuthUser
from core.context import get_current_tenant_id
from core.middleware.tenant import TenantMiddleware


async def _drive_middleware(middleware, scope) -> str:
    captured: dict[str, str] = {}

    async def app(scope, receive, send):
        captured["tenant"] = get_current_tenant_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware.app = app

    async def receive():
        return {"type": "http.request"}

    async def send(_message):
        return None

    await middleware(scope, receive, send)
    return captured["tenant"]


class TestTenantMiddleware:
    @pytest.mark.asyncio
    async def test_tenant_extraction_from_auth_user(self):
        user = AuthUser(user_id="u1", tenant_id="tenant-x", roles={AuthRole.USER})
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope = {"type": "http", "user": user}
        tenant = await _drive_middleware(middleware, scope)
        assert tenant == "tenant-x"

    @pytest.mark.asyncio
    async def test_default_tenant_if_no_user(self):
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope = {"type": "http"}
        tenant = await _drive_middleware(middleware, scope)
        assert tenant == "default"


class _FakeManager:
    """Stand-in AuthManager: counts verifications, returns a fixed user."""

    def __init__(self, user: AuthUser | None) -> None:
        self.user = user
        self.calls: list[str] = []

    async def authenticate(self, header: str) -> AuthUser | None:
        self.calls.append(header)
        return self.user


@pytest.fixture
def fake_auth(monkeypatch):
    """Patch the shared auth-memo helper's manager lookup."""
    from core.middleware import _auth_memo

    def _install(user: AuthUser | None) -> _FakeManager:
        manager = _FakeManager(user)
        monkeypatch.setattr(_auth_memo, "auth_manager", lambda: manager)
        return manager

    return _install


class TestTenantIdentityResolution:
    """The tenant must be derived from the credential, not from a later hook.

    ``scope['state']['user']`` is written by the route's auth *dependency*,
    which runs long after this middleware — and after every middleware inside
    it. Reading it here therefore resolved "default" for every real request.
    """

    @pytest.mark.asyncio
    async def test_bearer_token_sets_the_tenant(self, fake_auth):
        user = AuthUser(user_id="u9", tenant_id="acme", roles={AuthRole.USER})
        fake_auth(user)
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope = {
            "type": "http",
            "path": "/chat",
            "headers": [(b"authorization", b"Bearer tok")],
        }

        assert await _drive_middleware(middleware, scope) == "acme"

    @pytest.mark.asyncio
    async def test_api_key_header_sets_the_tenant(self, fake_auth):
        user = AuthUser(user_id="u9", tenant_id="beta", roles={AuthRole.USER})
        manager = fake_auth(user)
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope = {
            "type": "http",
            "path": "/chat",
            "headers": [(b"x-api-key", b"secret-key")],
        }

        assert await _drive_middleware(middleware, scope) == "beta"
        assert manager.calls == ["ApiKey secret-key"]

    @pytest.mark.asyncio
    async def test_verification_is_memoised_for_the_route_dependency(self, fake_auth):
        user = AuthUser(user_id="u9", tenant_id="acme", roles={AuthRole.USER})
        manager = fake_auth(user)
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope: dict = {
            "type": "http",
            "path": "/chat",
            "headers": [(b"authorization", b"Bearer tok")],
        }

        await _drive_middleware(middleware, scope)

        assert scope["state"]["_auth_memo"] == ("Bearer tok", id(manager), user)
        assert manager.calls == ["Bearer tok"]

    @pytest.mark.asyncio
    async def test_an_existing_memo_is_not_reverified(self, fake_auth):
        user = AuthUser(user_id="u9", tenant_id="acme", roles={AuthRole.USER})
        manager = fake_auth(user)
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope = {
            "type": "http",
            "path": "/chat",
            "headers": [(b"authorization", b"Bearer tok")],
            "state": {"_auth_memo": ("Bearer tok", id(manager), user)},
        }

        assert await _drive_middleware(middleware, scope) == "acme"
        assert manager.calls == []

    @pytest.mark.asyncio
    async def test_probe_paths_are_never_verified(self, fake_auth):
        manager = fake_auth(
            AuthUser(user_id="u9", tenant_id="acme", roles={AuthRole.USER})
        )
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope = {
            "type": "http",
            "path": "/health",
            "headers": [(b"authorization", b"Bearer tok")],
        }

        assert await _drive_middleware(middleware, scope) == "default"
        assert manager.calls == []

    @pytest.mark.asyncio
    async def test_a_failing_credential_falls_back_to_default(self, monkeypatch):
        from core.middleware import _auth_memo

        class _Boom:
            async def authenticate(self, header: str):
                raise RuntimeError("expired")

        monkeypatch.setattr(_auth_memo, "auth_manager", lambda: _Boom())
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope = {
            "type": "http",
            "path": "/chat",
            "headers": [(b"authorization", b"Bearer bad")],
        }

        assert await _drive_middleware(middleware, scope) == "default"


class TestReservedTenantIsRefused:
    """No token, however issued, gets a system-scoped session.

    ``system`` is the identity ``system_tenant_scope()`` binds for maintenance
    work, and migration ``010_system_tenant_rls_exemption`` grants it visibility
    of **every** tenant's rows. The middleware binds whatever the principal's
    ``tenant_id`` claim says, so a single careless issuer — a JWT claim, an
    API-key record, a provisioning call that took the id from input — would be a
    total compromise. Defence in depth: no path in this repository can currently
    mint such a principal (``TenantService.create_tenant`` now refuses the id),
    and the boundary refuses it anyway.
    """

    @staticmethod
    async def _drive(scope) -> tuple[list[dict], list[str]]:
        """Run the middleware, capturing the response and any tenant it bound."""
        from core.middleware.tenant import TenantMiddleware

        bound: list[str] = []
        messages: list[dict] = []

        async def app(scope, receive, send):  # pragma: no cover - must not run
            bound.append(get_current_tenant_id())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = TenantMiddleware(app=app)

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            messages.append(message)

        await middleware(scope, receive, send)
        return messages, bound

    @pytest.mark.asyncio
    async def test_a_token_claiming_system_is_rejected(self):
        user = AuthUser(user_id="u1", tenant_id="system", roles={AuthRole.USER})
        messages, bound = await self._drive(
            {"type": "http", "user": user, "path": "/v1/chat", "method": "POST"}
        )

        start = next(m for m in messages if m["type"] == "http.response.start")
        assert start["status"] == 403
        # The application never ran, so nothing downstream saw the identity.
        assert bound == []

    @pytest.mark.asyncio
    async def test_it_answers_problem_json(self):
        user = AuthUser(user_id="u1", tenant_id="system", roles={AuthRole.USER})
        messages, _ = await self._drive(
            {"type": "http", "user": user, "path": "/v1/chat", "method": "POST"}
        )

        start = next(m for m in messages if m["type"] == "http.response.start")
        headers = {k.decode(): v.decode() for k, v in start["headers"]}
        assert headers["content-type"].startswith("application/problem+json")

        import json

        body = b"".join(
            m.get("body", b"") for m in messages if m["type"] == "http.response.body"
        )
        document = json.loads(body)
        assert document["status"] == 403
        assert document["code"] == "reserved_tenant"
        assert "reserved" in document["detail"]

    @pytest.mark.asyncio
    async def test_the_rejection_is_audited(self, monkeypatch):
        """A principal arriving with a reserved claim is a security event, not a
        validation nit: someone issued that token."""
        recorded: list[tuple] = []

        from core.observability import audit as audit_module

        monkeypatch.setattr(
            audit_module,
            "audit_emit",
            lambda event_type, **kw: recorded.append((event_type, kw)),
        )

        user = AuthUser(user_id="u1", tenant_id="system", roles={AuthRole.USER})
        await self._drive(
            {"type": "http", "user": user, "path": "/v1/chat", "method": "POST"}
        )

        assert len(recorded) == 1
        event_type, kwargs = recorded[0]
        assert event_type is audit_module.AuditEventType.AUTH_FAILED
        assert kwargs["success"] is False
        assert kwargs["tenant_id"] == "system"
        assert kwargs["user_id"] == "u1"
        assert kwargs["action"] == "reserved_tenant_rejected"

    @pytest.mark.asyncio
    async def test_the_rejection_increments_the_security_counter(self, monkeypatch):
        """Both halves of the guard emit the same counter and label. The HTTP
        half did not, so ``security_events_total{reason="reserved_tenant"}``
        showed only WebSocket refusals — a dashboard that exists to surface
        exactly this event, silently missing half of it."""
        from core.middleware import _security_metrics

        labelled: list[str] = []

        class _Counter:
            def labels(self, **kwargs):
                labelled.append(kwargs["reason"])
                return self

            def inc(self):
                return None

        monkeypatch.setattr(_security_metrics, "SECURITY_EVENTS", _Counter())

        user = AuthUser(user_id="u1", tenant_id="system", roles={AuthRole.USER})
        await self._drive(
            {"type": "http", "user": user, "path": "/v1/chat", "method": "POST"}
        )

        assert labelled == ["reserved_tenant"]

    def test_both_halves_use_the_same_counter_label(self):
        """One label, so one query answers "is anyone trying this?"."""
        import inspect

        from core.middleware import security as security_module
        from core.middleware import tenant as tenant_module

        marker = 'SECURITY_EVENTS.labels(reason="reserved_tenant").inc()'
        assert marker in inspect.getsource(tenant_module)
        assert marker in inspect.getsource(security_module)

    @pytest.mark.asyncio
    async def test_an_ordinary_tenant_is_untouched(self):
        user = AuthUser(user_id="u1", tenant_id="acme", roles={AuthRole.USER})
        messages, bound = await self._drive({"type": "http", "user": user})

        start = next(m for m in messages if m["type"] == "http.response.start")
        assert start["status"] == 200
        assert bound == ["acme"]

    @pytest.mark.asyncio
    async def test_a_tenant_merely_containing_system_is_fine(self):
        """The check is equality against the reserved set, not a substring."""
        user = AuthUser(
            user_id="u1", tenant_id="system-integrators", roles={AuthRole.USER}
        )
        _, bound = await self._drive({"type": "http", "user": user})

        assert bound == ["system-integrators"]


def _calls_plain_setter(source: str) -> bool:
    """Whether *source* calls ``set_tenant_context`` directly.

    The negative lookbehind matters: ``reset_tenant_context`` ends with the same
    letters, and a plain substring test therefore reported every module that
    correctly restores its token.
    """
    import re

    return bool(re.search(r"(?<![A-Za-z_])set_tenant_context\s*\(", source))


class TestTheNonHttpScopeHole:
    """``TenantMiddleware`` never sees a WebSocket, so it cannot be the only guard.

    ``__call__`` returns early for any scope that is not ``http``, which is
    correct — there is no HTTP response to send on a socket. But the chat
    WebSocket route authenticates through ``SecurityManager.enforce_auth``, which
    binds ``user.tenant_id`` itself, so a token claiming ``system`` was refused on
    ``/chat/…`` and admitted on ``/chat/ws`` — binding the maintenance identity
    for the whole connection.
    """

    @pytest.mark.asyncio
    async def test_a_websocket_scope_passes_straight_through(self):
        """Pinning the shape of the hole: the middleware binds nothing here, so
        the second binding site is the one that must refuse."""
        from core.middleware.tenant import TenantMiddleware

        called: list[str] = []

        async def app(scope, receive, send):
            called.append(scope["type"])

        middleware = TenantMiddleware(app=app)
        user = AuthUser(user_id="u1", tenant_id="system", roles={AuthRole.USER})

        async def receive():  # pragma: no cover - never awaited
            return {"type": "websocket.connect"}

        async def send(_message):  # pragma: no cover - never awaited
            return None

        await middleware({"type": "websocket", "user": user}, receive, send)

        assert called == ["websocket"]

    @pytest.mark.asyncio
    async def test_enforce_auth_refuses_the_reserved_claim(self, monkeypatch):
        """The guard that actually covers the socket path."""
        from fastapi import HTTPException

        from core.context import get_current_tenant_id
        from core.middleware.security import SecurityManager

        manager = SecurityManager.__new__(SecurityManager)
        user = AuthUser(user_id="u1", tenant_id="system", roles={AuthRole.USER})

        with pytest.raises(HTTPException) as excinfo:
            manager._refuse_reserved_tenant(user, "10.0.0.1", "/chat/ws")

        assert excinfo.value.status_code == 403
        assert "reserved" in excinfo.value.detail
        # Nothing was bound on the way out.
        assert get_current_tenant_id() != "system"

    @pytest.mark.asyncio
    async def test_the_socket_refusal_is_audited(self, monkeypatch):
        from fastapi import HTTPException

        from core.middleware.security import SecurityManager
        from core.observability import audit as audit_module

        recorded: list[tuple] = []
        monkeypatch.setattr(
            audit_module,
            "audit_emit",
            lambda event_type, **kw: recorded.append((event_type, kw)),
        )

        manager = SecurityManager.__new__(SecurityManager)
        user = AuthUser(user_id="u1", tenant_id="system", roles={AuthRole.USER})

        with pytest.raises(HTTPException):
            manager._refuse_reserved_tenant(user, "10.0.0.1", "/chat/ws")

        assert len(recorded) == 1
        _event_type, kwargs = recorded[0]
        assert kwargs["action"] == "reserved_tenant_rejected"
        assert kwargs["tenant_id"] == "system"
        assert kwargs["ip_address"] == "10.0.0.1"

    def test_enforce_auth_binds_through_the_guarded_helper(self):
        """There is no longer a check-then-bind window to get the order wrong:
        the refusal happens *inside* the binding call. What this pins is that
        the principal is bound through that call and not the plain setter."""
        import inspect

        from core.middleware import security as security_module
        from core.middleware.security import SecurityManager

        source = inspect.getsource(SecurityManager.enforce_auth)
        assert "_bind_principal_tenant(user.tenant_id)" in source
        assert not _calls_plain_setter(inspect.getsource(security_module))

    def test_the_middleware_binds_through_the_guarded_helper_too(self):
        """Both in-repo principal binds use it, so the helper is the pattern a
        sibling checkout can follow rather than a rule it has to remember."""
        import inspect

        from core.middleware import tenant as tenant_module

        source = inspect.getsource(tenant_module)
        assert "bind_principal_tenant(tenant_id)" in source
        assert not _calls_plain_setter(source)
