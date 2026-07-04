"""Tests for the central per-plugin LLM policy.

Covers the plugin identity context var, the policy resolver seam
(``core.services.llm.policy``), the policy-aware service resolution
(``core.services.llm.runtime``), the model pin, the plugin-context middleware
and the orchestrator dispatch binding.
"""

import pytest

from core.config.services import LLMConfig
from core.context import (
    get_current_plugin,
    reset_plugin_context,
    set_plugin_context,
)
from core.middleware.plugin_context import PluginContextMiddleware
from core.services.llm import runtime
from core.services.llm.policy import (
    PluginLLMPolicy,
    resolve_active_llm_policy,
    resolve_plugin_llm_policy,
    set_plugin_llm_policy_resolver,
)
from core.services.llm.runtime import (
    api_key_for,
    get_llm_service,
    provider_configured,
    reset_llm_service,
)

# Env vars that can leak host credentials/config into LLMConfig (fields with
# validation_alias ignore same-named init kwargs, so tests must scrub these).
_LLM_ENV_VARS = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_OPENAI_API_KEY",
    "LLM_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "LLM_HUGGINGFACE_API_KEY",
    "HF_TOKEN",
)


def _config(**overrides) -> LLMConfig:
    """An LLMConfig with deterministic defaults (env scrubbed by the fixture)."""
    base = dict(provider="ollama", model="base-model", enable_cache=False)
    base.update(overrides)
    return LLMConfig(**base)


@pytest.fixture(autouse=True)
def _clean_policy_state(monkeypatch):
    """Isolate resolver + service singletons, scrub env, pin a hermetic config."""
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    cfg = _config()
    monkeypatch.setattr("core.services.llm.runtime.get_llm_config", lambda: cfg)
    monkeypatch.setattr("core.services.llm.service.get_llm_config", lambda: cfg)
    set_plugin_llm_policy_resolver(None)
    reset_llm_service()
    yield
    set_plugin_llm_policy_resolver(None)
    reset_llm_service()


class TestPluginContext:
    def test_default_is_none(self):
        assert get_current_plugin() is None

    def test_set_reset(self):
        token = set_plugin_context("my-plugin")
        try:
            assert get_current_plugin() == "my-plugin"
        finally:
            reset_plugin_context(token)
        assert get_current_plugin() is None

    async def test_async_propagation(self):
        token = set_plugin_context("async-plugin")
        try:

            async def inner() -> str | None:
                return get_current_plugin()

            assert await inner() == "async-plugin"
        finally:
            reset_plugin_context(token)


class TestPolicyResolution:
    def test_no_resolver_means_no_policy(self):
        assert resolve_plugin_llm_policy("anything") is None

    def test_resolver_raising_degrades_to_none(self):
        def boom(_: str) -> PluginLLMPolicy | None:
            raise RuntimeError("store down")

        set_plugin_llm_policy_resolver(boom)
        assert resolve_plugin_llm_policy("p") is None

    def test_empty_policy_is_dropped(self):
        set_plugin_llm_policy_resolver(lambda _n: PluginLLMPolicy())
        assert resolve_plugin_llm_policy("p") is None

    def test_unsupported_provider_is_dropped(self):
        set_plugin_llm_policy_resolver(
            lambda _n: PluginLLMPolicy(provider="skynet", model="t-800")
        )
        assert resolve_plugin_llm_policy("p") is None

    def test_valid_policy_passes_through(self):
        policy = PluginLLMPolicy(provider="ollama", model="m1")
        set_plugin_llm_policy_resolver(lambda n: policy if n == "p1" else None)
        assert resolve_plugin_llm_policy("p1") == policy
        assert resolve_plugin_llm_policy("p2") is None

    def test_active_policy_requires_bound_plugin(self):
        set_plugin_llm_policy_resolver(lambda _n: PluginLLMPolicy(model="m1"))
        assert resolve_active_llm_policy() is None
        token = set_plugin_context("p1")
        try:
            assert resolve_active_llm_policy() == PluginLLMPolicy(model="m1")
        finally:
            reset_plugin_context(token)


class TestPolicyAwareService:
    def test_no_policy_returns_default_singleton(self):
        assert get_llm_service() is get_llm_service()

    def test_unbound_context_ignores_policies(self):
        set_plugin_llm_policy_resolver(lambda _n: PluginLLMPolicy(model="pinned"))
        default = get_llm_service()  # no plugin bound
        assert default.config.model == "base-model"

    def test_model_pin_builds_cached_clone(self):
        set_plugin_llm_policy_resolver(
            lambda n: PluginLLMPolicy(model="pinned-model") if n == "p1" else None
        )
        default = get_llm_service()
        token = set_plugin_context("p1")
        try:
            pinned = get_llm_service()
            assert pinned is not default
            assert pinned.config.model == "pinned-model"
            assert pinned.config.provider == "ollama"
            # A pin is governance: it wins over per-call overrides too.
            assert pinned._resolve_model("caller-model") == "pinned-model"
            # Same policy target → same cached instance.
            assert get_llm_service() is pinned
        finally:
            reset_plugin_context(token)
        # Other plugins keep the default.
        token = set_plugin_context("p2")
        try:
            assert get_llm_service() is default
        finally:
            reset_plugin_context(token)

    def test_default_service_ignores_per_call_pin(self):
        default = get_llm_service()
        assert default._resolve_model("caller-model") == "caller-model"
        assert default._resolve_model(None) == "base-model"

    def test_cross_provider_pin_without_model_falls_back(self):
        set_plugin_llm_policy_resolver(lambda _n: PluginLLMPolicy(provider="anthropic"))
        token = set_plugin_context("p1")
        try:
            assert get_llm_service() is get_llm_service()
            assert get_llm_service().config.provider == "ollama"
        finally:
            reset_plugin_context(token)

    def test_unusable_provider_falls_back_to_default(self):
        # Anthropic pinned but no key configured → ctor fails → default served.
        set_plugin_llm_policy_resolver(
            lambda _n: PluginLLMPolicy(provider="anthropic", model="claude-x")
        )
        token = set_plugin_context("p1")
        try:
            assert get_llm_service().config.provider == "ollama"
        finally:
            reset_plugin_context(token)

    def test_reset_clears_policy_clones(self):
        set_plugin_llm_policy_resolver(lambda _n: PluginLLMPolicy(model="m1"))
        token = set_plugin_context("p1")
        try:
            first = get_llm_service()
            reset_llm_service()
            assert not runtime._policy_services
            assert get_llm_service() is not first
        finally:
            reset_plugin_context(token)


class TestConfigHelpers:
    # NB: credentialed fields carry a ``validation_alias``, so they can only be
    # populated via env (same-named init kwargs are ignored by pydantic).

    def test_primary_key_belongs_to_default_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "k-openai")
        cfg = _config(provider="openai")
        assert api_key_for(cfg, "openai").get_secret_value() == "k-openai"
        assert api_key_for(cfg, "anthropic") is None

    def test_dedicated_key_wins_for_other_providers(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "k1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k-ant")
        cfg = _config(provider="openai")
        assert api_key_for(cfg, "anthropic").get_secret_value() == "k-ant"

    def test_provider_configured(self, monkeypatch):
        cfg = _config(provider="ollama")
        assert provider_configured(cfg, "ollama") is True
        assert provider_configured(cfg, "anthropic") is False
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        assert provider_configured(_config(), "anthropic") is True
        assert provider_configured(_config(huggingface_local=True), "huggingface")


class _StubRegistry:
    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    def match_plugin_route(self, path: str) -> str | None:
        for prefix, name in self._mapping.items():
            if path == prefix or path.startswith(prefix + "/"):
                return name
        return None

    def get_flow_handler_owner(self, intent: str) -> str | None:
        return self._mapping.get(intent)


class _StubState:
    pass


class _StubApp:
    def __init__(self, registry):
        self.state = _StubState()
        self.state.plugin_registry = registry
        self.routes: list = []


class TestPluginContextMiddleware:
    async def _run(self, middleware, path: str, app) -> None:
        scope = {"type": "http", "path": path, "app": app}

        async def receive():  # pragma: no cover — never called
            return {"type": "http.request"}

        async def send(_message):
            return None

        await middleware(scope, receive, send)

    async def test_binds_plugin_for_matched_route(self):
        seen: list[str | None] = []

        async def inner(scope, receive, send):
            seen.append(get_current_plugin())

        app = _StubApp(_StubRegistry({"/api/myplugin": "myplugin"}))
        mw = PluginContextMiddleware(inner)
        await self._run(mw, "/api/myplugin/items", app)
        assert seen == ["myplugin"]
        assert get_current_plugin() is None  # reset after the request

    async def test_unmatched_route_stays_unbound(self):
        seen: list[str | None] = []

        async def inner(scope, receive, send):
            seen.append(get_current_plugin())

        app = _StubApp(_StubRegistry({"/api/myplugin": "myplugin"}))
        mw = PluginContextMiddleware(inner)
        await self._run(mw, "/api/other", app)
        assert seen == [None]

    async def test_non_http_scope_passthrough(self):
        called: list[bool] = []

        async def inner(scope, receive, send):
            called.append(True)

        mw = PluginContextMiddleware(inner)
        await mw({"type": "lifespan"}, None, None)
        assert called == [True]


class TestOrchestratorBinding:
    def test_bind_intent_plugin(self):
        from core.orchestration.mixins.execution import ExecutionMixin

        class Host(ExecutionMixin):
            plugin_registry = _StubRegistry({"my_intent": "owner-plugin"})

        host = Host()
        token = host._bind_intent_plugin("my_intent")
        try:
            assert get_current_plugin() == "owner-plugin"
        finally:
            reset_plugin_context(token)
        assert host._bind_intent_plugin("unknown_intent") is None

    def test_bind_without_registry_is_none(self):
        from core.orchestration.mixins.execution import ExecutionMixin

        class Host(ExecutionMixin):
            plugin_registry = None

        assert Host()._bind_intent_plugin("anything") is None
