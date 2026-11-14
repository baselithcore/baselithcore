"""A scoped credential reaches a data-plane route only with the route's scope."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from baselith.core.api.errors import install_error_handlers
from baselith.core.auth.types import AuthRole, AuthUser
from baselith.core.middleware import require_scope, require_user
from baselith.core.middleware.scope_gate import enforce_scoped_capability


def _client(role: str, user: AuthUser | None) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    async def gate(request: Request) -> str:
        request.state.user = user
        return role

    app.dependency_overrides[require_user] = gate

    @app.post("/chat", dependencies=[Depends(require_scope("chat:write"))])
    async def chat() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def _scoped(*scopes: str) -> AuthUser:
    return AuthUser(user_id="key:1", roles={AuthRole.SCOPED}, scopes=set(scopes))


def test_scoped_key_for_another_resource_is_refused() -> None:
    response = _client("scoped", _scoped("webhooks:read")).post("/chat")
    assert response.status_code == 403
    assert response.json()["code"] == "insufficient_scope"


@pytest.mark.parametrize("grant", ["chat:write", "chat:*", "*"])
def test_scoped_key_holding_the_capability_passes(grant: str) -> None:
    assert _client("scoped", _scoped(grant)).post("/chat").status_code == 200


@pytest.mark.parametrize("role", ["user", "job", "admin", "anonymous"])
def test_role_based_identities_are_left_to_the_role_gate(role: str) -> None:
    """No regression: only the pure-capability role is adjudicated by scope."""
    assert _client(role, None).post("/chat").status_code == 200


def test_scoped_role_without_an_identity_fails_closed() -> None:
    assert _client("scoped", None).post("/chat").status_code == 403


def test_the_data_plane_routes_declare_their_capability() -> None:
    """The routes that stopped at ``require_user`` now name a scope."""
    from baselith.plugins.api_routers.routes import async_runs, chat, feedback

    def gated(router: object) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for route in router.routes:  # type: ignore[attr-defined]
            calls = [d.call for d in route.dependant.dependencies]
            out[route.path] = any(
                getattr(c, "__qualname__", "").startswith("require_scope.")
                for c in calls
            )
        return out

    assert gated(chat.router) == {"/chat": True, "/chat/stream": True}
    assert gated(async_runs.router) == {
        "/agent/async": True,
        "/agent/status/{task_id}": True,
    }
    assert gated(feedback.router)["/feedback"] is True


def test_websocket_helper_shares_the_rule() -> None:
    from types import SimpleNamespace

    from baselith.core.auth import InsufficientScopeError

    conn = SimpleNamespace(state=SimpleNamespace(user=_scoped("webhooks:read")))
    with pytest.raises(InsufficientScopeError):
        enforce_scoped_capability(conn, "scoped", "chat:write")
    enforce_scoped_capability(conn, "user", "chat:write")
