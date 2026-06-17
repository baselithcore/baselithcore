"""Tests for capability-based authorization scopes."""

import pytest
from unittest.mock import AsyncMock, patch

from core.auth.api_keys import APIKeyValidator
from core.auth.jwt import JWTHandler
from core.auth.manager import AuthManager
from core.auth.scopes import (
    ROLE_SCOPES,
    SUPERUSER_SCOPE,
    effective_scopes,
    expand_roles,
    parse_scope_list,
    scope_satisfied,
    scopes_satisfied,
)
from core.auth.types import (
    AuthRole,
    AuthUser,
    InsufficientScopeError,
)
from core.config.security import SecurityConfig


# === Matching grammar ===
class TestScopeMatching:
    def test_exact_match(self):
        assert scope_satisfied({"chat:write"}, "chat:write")
        assert not scope_satisfied({"chat:read"}, "chat:write")

    def test_superuser_wildcard(self):
        assert scope_satisfied({SUPERUSER_SCOPE}, "anything:goes")
        assert scope_satisfied({SUPERUSER_SCOPE}, "tenants:manage")

    def test_resource_wildcard(self):
        assert scope_satisfied({"chat:*"}, "chat:write")
        assert scope_satisfied({"chat:*"}, "chat:read")
        # Resource wildcard must not leak to a different resource.
        assert not scope_satisfied({"chat:*"}, "memory:read")

    def test_empty_grant_denies(self):
        assert not scope_satisfied(set(), "chat:read")

    def test_scopes_satisfied_require_all(self):
        assert scopes_satisfied({"chat:*"}, ["chat:read", "chat:write"])
        assert not scopes_satisfied({"chat:read"}, ["chat:read", "chat:write"])

    def test_scopes_satisfied_any(self):
        assert scopes_satisfied(
            {"chat:read"}, ["chat:read", "memory:read"], require_all=False
        )
        assert not scopes_satisfied(
            {"feedback:write"}, ["chat:read", "memory:read"], require_all=False
        )

    def test_empty_requirement_always_satisfied(self):
        assert scopes_satisfied(set(), [])


# === Role expansion ===
class TestRoleExpansion:
    def test_admin_is_superuser(self):
        assert SUPERUSER_SCOPE in expand_roles({AuthRole.ADMIN})

    def test_user_scopes(self):
        scopes = expand_roles({AuthRole.USER})
        assert "chat:write" in scopes
        assert "tenants:manage" not in scopes

    def test_anonymous_has_nothing(self):
        assert expand_roles({AuthRole.ANONYMOUS}) == frozenset()

    def test_union_of_multiple_roles(self):
        scopes = expand_roles({AuthRole.GUEST, AuthRole.USER})
        # USER ⊃ GUEST here, but union must contain both role's scopes.
        assert "chat:write" in scopes
        assert "metrics:read" in scopes

    def test_every_role_has_mapping(self):
        for role in AuthRole:
            assert role in ROLE_SCOPES

    def test_effective_scopes_unions_explicit(self):
        eff = effective_scopes({AuthRole.GUEST}, {"webhooks:write"})
        assert "webhooks:write" in eff  # explicit
        assert "chat:read" in eff  # from GUEST role


# === AuthUser convenience ===
class TestAuthUserScopes:
    def test_admin_user_has_any_scope(self):
        admin = AuthUser(user_id="a", roles={AuthRole.ADMIN})
        assert admin.has_scope("tenants:manage")
        assert admin.has_scopes("chat:write", "webhooks:write")

    def test_user_lacks_control_plane(self):
        user = AuthUser(user_id="u", roles={AuthRole.USER})
        assert user.has_scope("chat:write")
        assert not user.has_scope("tenants:manage")

    def test_explicit_scope_grants_capability(self):
        scoped = AuthUser(
            user_id="s", roles={AuthRole.GUEST}, scopes={"webhooks:write"}
        )
        assert scoped.has_scope("webhooks:write")
        assert scoped.has_scope("chat:read")  # from GUEST
        assert not scoped.has_scope("chat:write")

    def test_has_scopes_require_all_false(self):
        user = AuthUser(user_id="u", roles={AuthRole.USER})
        assert user.has_scopes("chat:read", "tenants:manage", require_all=False)
        assert not user.has_scopes(
            "tenants:manage", "plugins:manage", require_all=False
        )


# === parse helper ===
def test_parse_scope_list():
    assert parse_scope_list("chat:read|chat:write|") == {"chat:read", "chat:write"}
    assert parse_scope_list("") == set()


# === Config parsing of scoped keys ===
class TestScopedKeyConfig:
    def test_parse_scoped_keys_string(self):
        c = SecurityConfig(
            SECRET_KEY="x" * 40,
            API_KEYS_SCOPED="sk_a=chat:read|chat:write,sk_b=webhooks:write",
        )
        assert c.api_keys_scoped == {
            "sk_a": {"chat:read", "chat:write"},
            "sk_b": {"webhooks:write"},
        }

    def test_malformed_entries_skipped(self):
        c = SecurityConfig(
            SECRET_KEY="x" * 40,
            API_KEYS_SCOPED="sk_a=chat:read, ,no_equals,sk_b=",
        )
        assert c.api_keys_scoped == {"sk_a": {"chat:read"}}

    def test_empty_default(self):
        c = SecurityConfig(SECRET_KEY="x" * 40)
        assert c.api_keys_scoped == {}


# === Scoped API keys end-to-end ===
class TestScopedApiKey:
    @pytest.mark.asyncio
    async def test_scoped_key_carries_capability(self):
        config = SecurityConfig(
            SECRET_KEY="x" * 40,
            API_KEYS_SCOPED="sk_hook=webhooks:write",
        )
        validator = APIKeyValidator(config=config)
        user = await validator.validate_key("sk_hook")
        assert user is not None
        assert user.has_scope("webhooks:write")
        # SERVICE role does not imply control-plane scopes.
        assert not user.has_scope("tenants:manage")

    @pytest.mark.asyncio
    async def test_register_key_with_scopes(self):
        config = SecurityConfig(SECRET_KEY="x" * 40)
        validator = APIKeyValidator(config=config)
        validator.register_key("k", "u", roles={AuthRole.GUEST}, scopes={"keys:manage"})
        user = await validator.validate_key("k")
        assert user is not None
        assert user.has_scope("keys:manage")


# === JWT scope round-trip ===
class TestJwtScopes:
    @pytest.mark.asyncio
    async def test_scopes_survive_token_round_trip(self):
        with patch("core.auth.jwt.create_redis_client") as factory:
            redis = AsyncMock()
            redis.get.return_value = None
            factory.return_value = redis
            handler = JWTHandler(secret_key="secret-with-at-least-thirty-two-chars")
            token = handler.create_token(
                "u1", roles={AuthRole.GUEST}, scopes={"webhooks:write"}
            )
            user = await handler.verify_token(token)
            assert user.has_scope("webhooks:write")
            assert "webhooks:write" in user.scopes

    @pytest.mark.asyncio
    async def test_no_scopes_claim_is_empty(self):
        with patch("core.auth.jwt.create_redis_client") as factory:
            redis = AsyncMock()
            redis.get.return_value = None
            factory.return_value = redis
            handler = JWTHandler(secret_key="secret-with-at-least-thirty-two-chars")
            token = handler.create_token("u1", roles={AuthRole.USER})
            user = await handler.verify_token(token)
            assert user.scopes == set()
            # Role-derived scopes still apply.
            assert user.has_scope("chat:write")


# === enforce_scopes / require_scopes ===
class TestEnforcement:
    @pytest.fixture
    def auth_manager(self):
        config = SecurityConfig(SECRET_KEY="x" * 40)
        with patch("core.auth.jwt.create_redis_client") as factory:
            redis = AsyncMock()
            redis.get.return_value = None
            factory.return_value = redis
            return AuthManager(config=config)

    def test_enforce_scopes_allows(self, auth_manager):
        admin = AuthUser(user_id="a", roles={AuthRole.ADMIN})
        auth_manager.enforce_scopes(admin, "tenants:manage")  # no raise

    def test_enforce_scopes_denies_missing(self, auth_manager):
        user = AuthUser(user_id="u", roles={AuthRole.USER})
        with pytest.raises(InsufficientScopeError) as ei:
            auth_manager.enforce_scopes(user, "tenants:manage")
        assert "tenants:manage" in ei.value.required

    def test_enforce_scopes_denies_anonymous(self, auth_manager):
        anon = AuthUser(user_id="anon", roles={AuthRole.ANONYMOUS})
        with pytest.raises(InsufficientScopeError):
            auth_manager.enforce_scopes(anon, "chat:read")

    def test_enforce_scopes_none_user(self, auth_manager):
        with pytest.raises(InsufficientScopeError):
            auth_manager.enforce_scopes(None, "chat:read")

    @pytest.mark.asyncio
    async def test_require_scopes_decorator_async(self, auth_manager):
        @auth_manager.require_scopes("webhooks:write")
        async def create_hook(user: AuthUser):
            return "ok"

        granted = AuthUser(user_id="s", roles={AuthRole.SERVICE})
        assert await create_hook(user=granted) == "ok"

        denied = AuthUser(user_id="u", roles={AuthRole.USER})
        with pytest.raises(InsufficientScopeError):
            await create_hook(user=denied)

    def test_require_scopes_decorator_sync(self, auth_manager):
        @auth_manager.require_scopes("metrics:read")
        def read_metrics(user: AuthUser):
            return "ok"

        guest = AuthUser(user_id="g", roles={AuthRole.GUEST})
        assert read_metrics(user=guest) == "ok"

    @pytest.mark.asyncio
    async def test_create_token_embeds_scopes(self, auth_manager):
        token = await auth_manager.create_token(
            "u1", roles={AuthRole.GUEST}, scopes={"webhooks:write"}
        )
        user = await auth_manager.authenticate(f"Bearer {token}")
        assert user.has_scope("webhooks:write")
