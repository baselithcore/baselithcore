"""RFC 8693 delegated tokens: scope narrowing must survive enforcement.

`resolve_exchange_scope` intersects the delegated scope set down — but if the
minted token still carries the user's roles and enforcement unions the
role-derived scopes back in (`roles:["admin"]` → `"*"`), the protocol layer's
"scope only narrows" property is a fiction. An agent-delegated identity
(``act`` with a ``client_id``) must be adjudicated by its explicit scopes
alone, exactly like a SCOPED api key. Admin impersonation (``act`` without
``client_id``) is a different feature with opposite semantics — the admin must
see exactly what the target sees — and keeps role expansion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from core.auth.types import AuthRole, AuthUser

AGENT_ACT = {"sub": "agent-1", "client_id": "agent-1"}
IMPERSONATION_ACT = {"sub": "admin-user"}  # no client_id → impersonation


def _delegated_user(**overrides) -> AuthUser:
    defaults = dict(
        user_id="alice",
        roles={AuthRole.ADMIN},
        scopes={"chat:read"},
        metadata={"act": AGENT_ACT},
    )
    defaults.update(overrides)
    return AuthUser(**defaults)


class TestEffectiveScopes:
    def test_agent_delegation_disables_role_expansion(self) -> None:
        user = _delegated_user()
        assert user.effective_scopes() == frozenset({"chat:read"})
        assert user.has_scope("chat:read")
        assert not user.has_scope("keys:manage")
        assert not user.has_scope("tenants:manage")

    def test_agent_delegation_with_no_scopes_grants_nothing(self) -> None:
        user = _delegated_user(scopes=set())
        assert user.effective_scopes() == frozenset()

    def test_impersonation_keeps_role_expansion(self) -> None:
        user = _delegated_user(metadata={"act": IMPERSONATION_ACT, "imp": True})
        assert user.has_scope("keys:manage")  # admin roles still expand

    def test_plain_identity_unchanged(self) -> None:
        user = _delegated_user(metadata={})
        assert user.has_scope("keys:manage")

    def test_is_agent_delegated_discriminator(self) -> None:
        assert _delegated_user().is_agent_delegated
        assert not _delegated_user(
            metadata={"act": IMPERSONATION_ACT}
        ).is_agent_delegated
        assert not _delegated_user(metadata={}).is_agent_delegated
        assert not _delegated_user(metadata={"act": "garbage"}).is_agent_delegated


class TestReservedDelegationClaims:
    """`act`/`may_act` decide re-delegation refusal and advertise the actor
    allowlist: caller-supplied extras must never be able to forge them."""

    def _handler(self):
        from core.auth.jwt import JWTHandler

        with patch("core.auth.jwt.create_redis_client") as redis_factory:
            redis_factory.return_value = AsyncMock(get=AsyncMock(return_value=None))
            return JWTHandler(secret_key="secret-with-at-least-thirty-two-characters!")

    def _decode(self, token: str) -> dict:
        import jwt as pyjwt

        return pyjwt.decode(token, options={"verify_signature": False})

    def test_act_in_extra_claims_is_stripped(self) -> None:
        handler = self._handler()
        token = handler.create_token(
            "u-1", extra_claims={"act": AGENT_ACT, "may_act": {"client_id": ["x"]}}
        )
        payload = self._decode(token)
        assert "act" not in payload
        assert "may_act" not in payload

    def test_first_class_act_parameter_mints_the_claim(self) -> None:
        handler = self._handler()
        token = handler.create_token("u-1", act=AGENT_ACT)
        assert self._decode(token)["act"] == AGENT_ACT
