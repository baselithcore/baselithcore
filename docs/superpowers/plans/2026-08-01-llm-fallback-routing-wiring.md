# LLM Fallback + Routing Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the orphaned `FallbackChain` and `ModelRouter` primitives into the live `LLMService` request path, and enable native tool-calling by default.

**Architecture:** `LLMService.generate_response` gains a config-driven cross-provider fallback chain (primary provider + ordered `provider:model` fallbacks, each a cached `LLMService` clone, circuit-breaker-aware, budget errors fatal). Model resolution gains an optional cost-aware routing step (`task_category` hint → `ModelRouter`), slotted between per-call override and config default. `enable_native_tools` flips to default-on (the structured path already guards on `supports_native_tools`, so providers without native APIs still use prompt coercion).

**Tech Stack:** Python 3.12, Pydantic settings, pytest (asyncio_mode=auto), existing `core/models/fallback.py` + `core/models/routing.py` + `core/resilience/circuit_breaker.py`.

## Global Constraints

- File size cap: 500 lines per module (`service.py` is at 482 — new logic goes in a new module).
- Mock LLMs in unit tests; no network.
- Every new module exported via its package `__init__.py`.
- Docs sync: update `mkdocs-site/docs/core-modules/models.md` + `services.md` in the same change.
- Secrets stay `SecretStr`; fallback credentials resolved via existing `core.services.llm.runtime.api_key_for`.
- Retry discipline: ONE retry layer (`_generate_with_retry` per provider stage). The chain adds provider *fallthrough*, never extra retries.
- Budget/deadline errors (`core.orchestration.limits.BudgetExceededError`, `core.middleware.cost_control.BudgetExceededError`, `core.services.llm.exceptions.BudgetExceededError`) must NOT trigger fallthrough — they are fatal.
- Commit after each task; conventional-commit messages.

---

### Task 1: `FallbackChain` fatal-exception support

**Files:**

- Modify: `core/models/fallback.py`
- Test: `tests/unit/core/models/test_fallback.py`

**Interfaces:**

- Produces: `FallbackChain(providers, fatal_exceptions: tuple[type[BaseException], ...] = ())` — exceptions in `fatal_exceptions` re-raise immediately from `run()` instead of falling through to the next provider.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/core/models/test_fallback.py`)

```python
class TestFatalExceptions:
    async def test_fatal_exception_reraises_without_fallthrough(self):
        class Fatal(RuntimeError):
            pass

        secondary_called = False

        async def primary():
            raise Fatal("budget blown")

        async def secondary():
            nonlocal secondary_called
            secondary_called = True
            return "ok"

        chain = FallbackChain(
            [Provider(name="p1", call=primary), Provider(name="p2", call=secondary)],
            fatal_exceptions=(Fatal,),
        )
        with pytest.raises(Fatal):
            await chain.run()
        assert secondary_called is False

    async def test_non_fatal_still_falls_through(self):
        async def primary():
            raise ValueError("boom")

        async def secondary():
            return "ok"

        chain = FallbackChain(
            [Provider(name="p1", call=primary), Provider(name="p2", call=secondary)],
            fatal_exceptions=(KeyError,),
        )
        outcome = await chain.run()
        assert outcome.result == "ok"
        assert outcome.provider == "p2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/core/models/test_fallback.py -k Fatal -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'fatal_exceptions'`

- [ ] **Step 3: Implement** — in `core/models/fallback.py`:

```python
    def __init__(
        self,
        providers: list[Provider[T]],
        fatal_exceptions: tuple[type[BaseException], ...] = (),
    ) -> None:
        if not providers:
            raise ValueError("FallbackChain requires at least one provider")
        names = [p.name for p in providers]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate provider names in chain: {names}")
        self._providers = providers
        self._fatal_exceptions = fatal_exceptions
```

and in `run()`, change the `except Exception as exc:` block to re-raise fatals first:

```python
            except Exception as exc:
                if isinstance(exc, self._fatal_exceptions):
                    # Fatal for the whole request (budget/deadline blown):
                    # falling through would spend money the caller no longer has.
                    raise
                attempts.append(
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/unit/core/models/test_fallback.py -v`
Expected: all PASS (old + new)

- [ ] **Step 5: Commit**

```bash
git add core/models/fallback.py tests/unit/core/models/test_fallback.py
git commit -m "feat(models): fatal-exception passthrough in FallbackChain"
```

---

### Task 2: LLM config — fallback chain, routing, native tools default-on

**Files:**

- Modify: `core/config/services.py` (LLMConfig)
- Modify: `.env.example`
- Test: `tests/unit/core/services/llm/test_llm_config_wiring.py` (create)

**Interfaces:**

- Produces on `LLMConfig`:
    - `enable_native_tools: bool = True` (flipped default)
    - `fallback_chain: str = ""` — comma-separated ordered `provider:model` entries (env `LLM_FALLBACK_CHAIN`, e.g. `"openai:gpt-4o-mini,ollama:llama3.2"`). Empty = fallback disabled.
    - `routing_enabled: bool = False` (env `LLM_ROUTING_ENABLED`)
    - `routing_policy: str = ""` — JSON object mapping `TaskCategory` value → model id (env `LLM_ROUTING_POLICY`); empty = `RoutingPolicy()` defaults.

- [ ] **Step 1: Write the failing test** (`tests/unit/core/services/llm/test_llm_config_wiring.py`)

```python
"""Config surface for fallback chain + routing wiring."""

from core.config.services import LLMConfig


class TestLLMConfigWiring:
    def test_native_tools_default_on(self):
        assert LLMConfig().enable_native_tools is True

    def test_fallback_chain_default_empty(self):
        assert LLMConfig().fallback_chain == ""

    def test_fallback_chain_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_FALLBACK_CHAIN", "openai:gpt-4o-mini,ollama:llama3.2")
        assert LLMConfig().fallback_chain == "openai:gpt-4o-mini,ollama:llama3.2"

    def test_routing_defaults(self):
        config = LLMConfig()
        assert config.routing_enabled is False
        assert config.routing_policy == ""

    def test_routing_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_ROUTING_ENABLED", "true")
        monkeypatch.setenv("LLM_ROUTING_POLICY", '{"planning": "gpt-4o"}')
        config = LLMConfig()
        assert config.routing_enabled is True
        assert config.routing_policy == '{"planning": "gpt-4o"}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/core/services/llm/test_llm_config_wiring.py -v`
Expected: FAIL — `enable_native_tools` is False, unknown fields.

- [ ] **Step 3: Implement** — in `LLMConfig` (`core/config/services.py`), replace the `enable_native_tools` block with:

```python
    # == Native tool-calling / structured outputs ==
    # On by default: the structured path still guards on the provider's
    # ``supports_native_tools`` flag, so providers without a native API keep
    # using the prompt-coercion fallback. Set false to force coercion.
    enable_native_tools: bool = Field(
        default=True,
        description="Use providers' native tool-calling / structured-output APIs "
        "in LLMService.generate() (falls back to prompt coercion when off).",
    )

    # == Cross-provider fallback chain ==
    # Ordered "provider:model" pairs tried when the primary provider fails or
    # its circuit breaker is open. Budget/deadline errors never fall through.
    # Each entry needs its provider's dedicated credentials configured.
    fallback_chain: str = Field(
        default="",
        description="Comma-separated ordered 'provider:model' fallback entries "
        "(e.g. 'openai:gpt-4o-mini,ollama:llama3.2'). Empty disables fallback.",
    )

    # == Cost-aware model routing ==
    # When enabled, callers may pass task_category to generate_response();
    # the router picks a model tier for that category. Explicit per-call
    # model= and policy-pinned models always win over routing.
    routing_enabled: bool = Field(
        default=False,
        description="Enable cost-aware model routing by task category.",
    )

    routing_policy: str = Field(
        default="",
        description="JSON object mapping task category to model id "
        '(e.g. \'{"planning": "gpt-4o", "classification": "gpt-4o-mini"}\'). '
        "Empty uses the built-in default policy.",
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/unit/core/services/llm/test_llm_config_wiring.py tests/unit/core/services/llm/test_llm_service.py -v`
Expected: PASS. If any existing test asserts `enable_native_tools is False` as the default, update that test to the new default in this task.

- [ ] **Step 5: Add env vars to `.env.example`** under the LLM section:

```bash
# Cross-provider fallback chain (ordered "provider:model" pairs; empty = off)
# LLM_FALLBACK_CHAIN=openai:gpt-4o-mini,ollama:llama3.2
# Cost-aware model routing by task category (see core/models/routing.py)
# LLM_ROUTING_ENABLED=false
# LLM_ROUTING_POLICY={"planning": "gpt-4o", "classification": "gpt-4o-mini"}
```

- [ ] **Step 6: Commit**

```bash
git add core/config/services.py .env.example tests/unit/core/services/llm/test_llm_config_wiring.py
git commit -m "feat(config): fallback chain + routing settings, native tools on by default"
```

---

### Task 3: Fallback runtime module

**Files:**

- Create: `core/services/llm/fallback_runtime.py`
- Modify: `core/services/llm/__init__.py` (export)
- Test: `tests/unit/core/services/llm/test_fallback_runtime.py` (create)

**Interfaces:**

- Consumes: `FallbackChain`, `Provider` (Task 1), `LLMConfig.fallback_chain` (Task 2), `api_key_for` from `core.services.llm.runtime`, `get_circuit_breaker`/`CircuitState` from `core.resilience.circuit_breaker`.
- Produces:
    - `parse_fallback_chain(spec: str) -> list[tuple[str, str]]` — validated `(provider, model)` pairs; raises `ValueError` on malformed entries or unsupported providers; drops duplicates of the primary later at build time.
    - `run_with_fallback(service: "LLMService", prompt: str, model: str, json_mode: bool, **kwargs) -> tuple[str, int, str]` — returns `(content, tokens_used, serving_provider_name)`. Primary stage = `service._generate_with_retry`; each fallback stage = a cached clone service's `_generate_with_retry` with that entry's model. Circuit-breaker `is_open` check per stage. Fatal: the three budget error types.

- [ ] **Step 1: Write the failing tests** (`tests/unit/core/services/llm/test_fallback_runtime.py`)

```python
"""Unit tests for the LLMService cross-provider fallback runtime."""

from unittest.mock import AsyncMock, patch

import pytest

from core.services.llm.fallback_runtime import (
    parse_fallback_chain,
    run_with_fallback,
)


class TestParseFallbackChain:
    def test_empty_spec_returns_empty_list(self):
        assert parse_fallback_chain("") == []
        assert parse_fallback_chain("   ") == []

    def test_parses_ordered_pairs(self):
        assert parse_fallback_chain("openai:gpt-4o-mini, ollama:llama3.2") == [
            ("openai", "gpt-4o-mini"),
            ("ollama", "llama3.2"),
        ]

    def test_rejects_malformed_entry(self):
        with pytest.raises(ValueError, match="provider:model"):
            parse_fallback_chain("openai")

    def test_rejects_unsupported_provider(self):
        with pytest.raises(ValueError, match="Unsupported"):
            parse_fallback_chain("bedrock:claude")


def _make_service(fallback_chain="openai:gpt-4o-mini"):
    """LLMService with mocked provider construction and a fallback chain."""
    from core.config.services import LLMConfig
    from core.services.llm.service import LLMService

    config = LLMConfig(
        provider="ollama", model="llama3.2", fallback_chain=fallback_chain
    )
    with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
        return LLMService(config=config, enable_cache=False)


class TestRunWithFallback:
    async def test_primary_success_no_fallback(self):
        service = _make_service()
        with patch.object(
            service, "_generate_with_retry", AsyncMock(return_value=("hi", 7))
        ) as primary:
            content, tokens, provider_name = await run_with_fallback(
                service, "p", model="llama3.2", json_mode=False
            )
        assert (content, tokens, provider_name) == ("hi", 7, "ollama")
        primary.assert_awaited_once()

    async def test_falls_through_to_secondary_on_primary_failure(self):
        service = _make_service()
        secondary = AsyncMock()
        secondary._generate_with_retry = AsyncMock(return_value=("saved", 3))
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch(
                "core.services.llm.fallback_runtime._clone_service",
                return_value=secondary,
            ),
        ):
            content, tokens, provider_name = await run_with_fallback(
                service, "p", model="llama3.2", json_mode=False
            )
        assert (content, tokens, provider_name) == ("saved", 3, "openai")
        # The fallback entry's model wins over the primary's model.
        assert (
            secondary._generate_with_retry.await_args.kwargs["model"] == "gpt-4o-mini"
        )

    async def test_budget_error_is_fatal_no_fallthrough(self):
        from core.orchestration.limits import BudgetExceededError, LoopBudget

        service = _make_service()
        budget = LoopBudget()
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(
                    side_effect=BudgetExceededError("max_seconds", budget.snapshot())
                ),
            ),
            patch(
                "core.services.llm.fallback_runtime._clone_service"
            ) as clone,
        ):
            with pytest.raises(BudgetExceededError):
                await run_with_fallback(
                    service, "p", model="llama3.2", json_mode=False
                )
        clone.assert_not_called()

    async def test_all_failed_raises_llm_provider_error(self):
        from core.services.llm.exceptions import LLMProviderError

        service = _make_service()
        secondary = AsyncMock()
        secondary._generate_with_retry = AsyncMock(side_effect=RuntimeError("also down"))
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch(
                "core.services.llm.fallback_runtime._clone_service",
                return_value=secondary,
            ),
        ):
            with pytest.raises(LLMProviderError, match="All providers failed"):
                await run_with_fallback(
                    service, "p", model="llama3.2", json_mode=False
                )
```

Note: check `LoopBudget().snapshot()` exists in `core/orchestration/limits.py` before using it in the budget test; if the constructor differs, build the error with whatever minimal valid arguments that module exposes (read the class first — do not guess).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/core/services/llm/test_fallback_runtime.py -v`
Expected: FAIL — `ModuleNotFoundError: core.services.llm.fallback_runtime`

- [ ] **Step 3: Implement** (`core/services/llm/fallback_runtime.py`)

```python
"""Cross-provider fallback wiring for the LLM service.

Builds a :class:`core.models.fallback.FallbackChain` around the primary
provider call plus config-declared ``provider:model`` fallback stages
(``LLMConfig.fallback_chain``). Each fallback stage is a cached
:class:`~core.services.llm.service.LLMService` clone (same config surface,
dedicated credentials via :func:`core.services.llm.runtime.api_key_for`), so
timeouts and retry discipline stay identical to the primary path.

Open circuit breakers are skipped without paying for a doomed call. Budget
and deadline errors are fatal: the request is out of money or time, so
falling through to a second provider would double-spend, not recover.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from core.models.fallback import AllProvidersFailedError, FallbackChain, Provider
from core.observability.logging import get_logger
from core.resilience.circuit_breaker import CircuitState, get_circuit_breaker

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

_SUPPORTED_PROVIDERS = ("openai", "ollama", "huggingface", "anthropic")

# Fallback-stage service clones, shared process-wide and keyed by
# (provider, model) — mirrors the policy-clone cache in ``runtime``.
_fallback_services: dict[tuple[str, str], LLMService] = {}
_lock = threading.Lock()


def parse_fallback_chain(spec: str) -> list[tuple[str, str]]:
    """Parse ``LLMConfig.fallback_chain`` into ordered (provider, model) pairs."""
    entries: list[tuple[str, str]] = []
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        provider, sep, model = item.partition(":")
        provider, model = provider.strip(), model.strip()
        if not sep or not provider or not model:
            raise ValueError(
                f"Malformed fallback entry {item!r}: expected 'provider:model'"
            )
        if provider not in _SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported fallback provider: {provider}")
        entries.append((provider, model))
    return entries


def _breaker_open(provider: str) -> bool:
    """Whether *provider*'s circuit breaker is currently OPEN."""
    return get_circuit_breaker(f"{provider}_provider").state == CircuitState.OPEN


def _clone_service(base: LLMService, provider: str, model: str) -> LLMService:
    """A cached LLMService clone for a fallback stage (built on first use)."""
    key = (provider, model)
    service = _fallback_services.get(key)
    if service is not None:
        return service
    with _lock:
        service = _fallback_services.get(key)
        if service is not None:
            return service
        from core.services.llm.runtime import api_key_for
        from core.services.llm.service import LLMService

        config = base.config.model_copy(
            update={
                "provider": provider,
                "model": model,
                "api_key": api_key_for(base.config, provider),
                # A clone must never recurse into its own fallback chain.
                "fallback_chain": "",
            }
        )
        service = LLMService(config=config, enable_cache=False)
        _fallback_services[key] = service
        return service


def reset_fallback_services() -> None:
    """Clear the fallback-stage clone cache (tests / credential rotation)."""
    with _lock:
        _fallback_services.clear()


async def run_with_fallback(
    service: LLMService,
    prompt: str,
    model: str,
    json_mode: bool,
    **kwargs: object,
) -> tuple[str, int, str]:
    """Run the primary provider, falling through the configured chain on failure.

    Returns ``(content, tokens_used, serving_provider_name)`` so the caller
    can attribute metrics to the provider that actually served the request.
    """
    from core.middleware.cost_control import (
        BudgetExceededError as MiddlewareBudgetExceededError,
    )
    from core.orchestration.limits import BudgetExceededError as LoopBudgetExceededError
    from core.services.llm.exceptions import (
        BudgetExceededError,
        LLMProviderError,
    )

    primary_name = service.config.provider

    async def _primary() -> tuple[str, int]:
        return await service._generate_with_retry(
            prompt=prompt, model=model, json_mode=json_mode, **kwargs
        )

    stages: list[Provider[tuple[str, int]]] = [
        Provider(
            name=primary_name,
            call=_primary,
            is_open=lambda: _breaker_open(primary_name),
        )
    ]
    for fb_provider, fb_model in parse_fallback_chain(service.config.fallback_chain):
        if fb_provider == primary_name and fb_model == model:
            continue  # identical to the primary stage — nothing to gain

        async def _stage(
            _provider: str = fb_provider, _model: str = fb_model
        ) -> tuple[str, int]:
            clone = _clone_service(service, _provider, _model)
            return await clone._generate_with_retry(
                prompt=prompt, model=_model, json_mode=json_mode, **kwargs
            )

        stages.append(
            Provider(
                name=fb_provider,
                call=_stage,
                is_open=lambda _provider=fb_provider: _breaker_open(_provider),
            )
        )

    chain: FallbackChain[tuple[str, int]] = FallbackChain(
        stages,
        fatal_exceptions=(
            BudgetExceededError,
            MiddlewareBudgetExceededError,
            LoopBudgetExceededError,
        ),
    )
    try:
        outcome = await chain.run()
    except AllProvidersFailedError as exc:
        raise LLMProviderError(str(exc)) from exc
    if outcome.provider != primary_name:
        logger.warning(
            "llm_fallback_served",
            extra={"provider": outcome.provider, "primary": primary_name},
        )
    content, tokens = outcome.result
    return content, tokens, outcome.provider
```

Add to `core/services/llm/__init__.py` exports: `parse_fallback_chain`, `run_with_fallback`, `reset_fallback_services` (follow the file's existing export style).

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/unit/core/services/llm/test_fallback_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/services/llm/fallback_runtime.py core/services/llm/__init__.py tests/unit/core/services/llm/test_fallback_runtime.py
git commit -m "feat(llm): cross-provider fallback runtime for LLMService"
```

---

### Task 4: Wire fallback + routing into `LLMService.generate_response`

**Files:**

- Modify: `core/services/llm/service.py`
- Test: extend `tests/unit/core/services/llm/test_llm_service.py`

**Interfaces:**

- Consumes: `run_with_fallback` (Task 3), `ModelRouter`/`RoutingPolicy`/`TaskCategory` from `core.models.routing`, config fields (Task 2).
- Produces:
    - `generate_response(..., task_category: str | None = None)` — new optional kwarg, also added to `generate()` and passed through.
    - `_resolve_model(model, task_category=None)` — precedence: pinned > explicit `model=` > routed (when `routing_enabled` and category valid) > `config.model`.
    - Fallback active in `generate_response` whenever `config.fallback_chain` is non-empty; span gains `gen_ai.baselith.serving_provider` and metrics use the serving provider's system label.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/core/services/llm/test_llm_service.py`, using that file's existing service-fixture idioms — reuse its mock-provider setup)

```python
class TestModelRoutingResolution:
    def _service(self, **config_kwargs):
        from unittest.mock import AsyncMock, patch

        from core.config.services import LLMConfig
        from core.services.llm.service import LLMService

        config = LLMConfig(provider="ollama", model="llama3.2", **config_kwargs)
        with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
            return LLMService(config=config, enable_cache=False)

    def test_routing_disabled_ignores_category(self):
        service = self._service(routing_enabled=False)
        assert service._resolve_model(None, task_category="planning") == "llama3.2"

    def test_routing_selects_policy_model(self):
        service = self._service(
            routing_enabled=True,
            routing_policy='{"planning": "big-model", "classification": "small-model"}',
        )
        assert service._resolve_model(None, task_category="planning") == "big-model"
        assert (
            service._resolve_model(None, task_category="classification")
            == "small-model"
        )

    def test_explicit_model_beats_routing(self):
        service = self._service(
            routing_enabled=True, routing_policy='{"planning": "big-model"}'
        )
        assert service._resolve_model("pinned-call", task_category="planning") == (
            "pinned-call"
        )

    def test_unknown_category_falls_back_to_config_model(self):
        service = self._service(routing_enabled=True, routing_policy="{}")
        assert service._resolve_model(None, task_category="nonsense") == "llama3.2"


class TestFallbackWiring:
    async def test_generate_response_uses_fallback_runtime_when_configured(self):
        from unittest.mock import AsyncMock, patch

        from core.config.services import LLMConfig
        from core.services.llm.service import LLMService

        config = LLMConfig(
            provider="ollama", model="llama3.2", fallback_chain="openai:gpt-4o-mini"
        )
        with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
            service = LLMService(config=config, enable_cache=False)
        with patch(
            "core.services.llm.service.run_with_fallback",
            AsyncMock(return_value=("saved", 5, "openai")),
        ) as rwf:
            result = await service.generate_response("hello")
        assert result == "saved"
        rwf.assert_awaited_once()

    async def test_no_chain_configured_keeps_direct_path(self):
        from unittest.mock import AsyncMock, patch

        from core.config.services import LLMConfig
        from core.services.llm.service import LLMService

        config = LLMConfig(provider="ollama", model="llama3.2", fallback_chain="")
        with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
            service = LLMService(config=config, enable_cache=False)
        with (
            patch.object(
                service, "_generate_with_retry", AsyncMock(return_value=("hi", 3))
            ) as direct,
            patch("core.services.llm.service.run_with_fallback", AsyncMock()) as rwf,
        ):
            result = await service.generate_response("hello")
        assert result == "hi"
        direct.assert_awaited_once()
        rwf.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/core/services/llm/test_llm_service.py -k "Routing or FallbackWiring" -v`
Expected: FAIL — no `task_category` kwarg, no `run_with_fallback` symbol in `service`.

- [ ] **Step 3: Implement** in `core/services/llm/service.py`:

3a. Import at top (module level, alongside existing service imports):

```python
from core.services.llm.fallback_runtime import run_with_fallback
```

3b. Router construction at the end of `__init__` (before the closing `logger.info`):

```python
        # Cost-aware model router (opt-in). Policy JSON maps TaskCategory
        # values to model ids; invalid JSON disables routing loudly.
        self._router = None
        if self.config.routing_enabled:
            self._router = self._build_router()
```

3c. New private methods after `_resolve_model`:

```python
    def _build_router(self):
        """Build the ModelRouter from ``LLMConfig.routing_policy`` JSON."""
        import json as _json

        from core.models.routing import ModelRouter, RoutingPolicy, TaskCategory

        if not self.config.routing_policy:
            return ModelRouter()
        try:
            raw = _json.loads(self.config.routing_policy)
            primary = {TaskCategory(cat): model for cat, model in raw.items()}
        except (ValueError, KeyError) as exc:
            logger.error(f"Invalid LLM_ROUTING_POLICY, routing disabled: {exc}")
            return None
        # Unlisted categories fall back to the config default model.
        return ModelRouter(RoutingPolicy(primary=primary, complexity_upgrade={}))

    def _routed_model(self, task_category: str | None) -> str | None:
        """Model chosen by the router for *task_category*, or None."""
        if self._router is None or not task_category:
            return None
        from core.models.routing import TaskCategory

        try:
            category = TaskCategory(task_category)
            return self._router.select(category).model_id
        except (ValueError, KeyError):
            # Unknown category or category absent from the policy: routing is
            # a hint, never an error — fall back to the config default.
            return None
```

3d. Replace `_resolve_model`:

```python
    def _resolve_model(self, model: str | None, task_category: str | None = None) -> str:
        """Effective model: pinned > per-call > routed > config default."""
        return (
            self._pinned_model
            or model
            or self._routed_model(task_category)
            or self.config.model
        )
```

3e. `generate_response` signature gains `task_category: str | None = None` (documented in the docstring: "Optional task category hint for cost-aware routing (see core.models.routing.TaskCategory); ignored unless routing is enabled"). The `model = self._resolve_model(model)` line becomes `model = self._resolve_model(model, task_category)`.

3f. In `_generate_and_cache`, replace the direct call:

```python
                started = time.perf_counter()
                serving_provider = self.config.provider
                if self.config.fallback_chain:
                    content, tokens_used, serving_provider = await run_with_fallback(
                        self, prompt=prompt, model=model, json_mode=json, **extra_kwargs
                    )
                else:
                    content, tokens_used = await self._generate_with_retry(
                        prompt=prompt, model=model, json_mode=json, **extra_kwargs
                    )
```

and attribute metrics to the serving provider — the `record_genai_metrics` call's first argument becomes `_gen_ai_system(serving_provider)`, plus after the usage span attributes add:

```python
                span.set_attribute("gen_ai.baselith.serving_provider", serving_provider)
```

3g. `generate()` also gains `task_category: str | None = None` and forwards it to `generate_structured` **only if** that function's signature is extended in this task — instead, keep scope: `generate()` resolves nothing itself (structured.py calls `service._resolve_model`? verify). Check `core/services/llm/structured.py` for its model-resolution line; if it calls `service._resolve_model(model)`, extend that call site with a `task_category` parameter threaded through `generate()`. If it resolves differently, leave `generate()` untouched and note it in the docs task.

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/unit/core/services/llm/ tests/unit/core/models/test_fallback.py tests/unit/core/models/test_routing.py -v`
Expected: PASS

- [ ] **Step 5: Run wider gates**

Run: `ruff check core/services/llm/ core/config/services.py core/models/fallback.py && mypy core/ 2>&1 | tail -5 && python -m pytest tests/unit/core/services/ -q 2>&1 | tail -3`
Expected: clean ruff, mypy no new errors, tests pass.

- [ ] **Step 6: Commit**

```bash
git add core/services/llm/service.py tests/unit/core/services/llm/test_llm_service.py
git commit -m "feat(llm): wire fallback chain and cost-aware routing into LLMService"
```

---

### Task 5: Route the intent classifier through the CLASSIFICATION tier

**Files:**

- Modify: `core/orchestration/intent_classifier.py` (`_classify_with_llm`)
- Test: extend the existing intent-classifier test module (find it: `grep -rl "intent_classifier" tests/unit/`)

**Interfaces:**

- Consumes: `generate_response(..., task_category=...)` from Task 4.
- Produces: the classifier's LLM call passes `task_category="classification"` so deployments with routing enabled serve intent classification from the cheap tier.

- [ ] **Step 1: Read `_classify_with_llm` in `core/orchestration/intent_classifier.py`** (around line 243) and find the `generate_response(` call site.

- [ ] **Step 2: Write the failing test** (in the intent-classifier test module, following its existing mock style):

```python
async def test_llm_classification_passes_classification_category(classifier_with_llm):
    """The classifier's LLM call must hint the cheap routing tier."""
    classifier, mock_llm = classifier_with_llm  # adapt to the module's fixtures
    await classifier.classify_with_confidence("what is the weather")
    call_kwargs = mock_llm.generate_response.await_args.kwargs
    assert call_kwargs.get("task_category") == "classification"
```

(Adapt fixture names to the module's actual fixtures — read the file first; if no fixture provides a mocked `llm_service`, build the classifier the way its other LLM tests do.)

- [ ] **Step 3: Run test to verify it fails**

Expected: FAIL — `task_category` not in call kwargs.

- [ ] **Step 4: Implement** — add `task_category="classification"` to the `generate_response(...)` call inside `_classify_with_llm`.

- [ ] **Step 5: Run the intent-classifier test module**

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/orchestration/intent_classifier.py tests/unit/core/orchestration/
git commit -m "feat(orchestration): route intent classification through cheap model tier"
```

---

### Task 6: Docs sync + full gates

**Files:**

- Modify: `mkdocs-site/docs/core-modules/models.md` (routing + fallback are now wired — document config, precedence, fatal semantics)
- Modify: `mkdocs-site/docs/core-modules/services.md` (LLMConfig new fields; `enable_native_tools` new default)
- Modify: `CHANGELOG.md` if the repo convention is manual entries (check: recent entries are semantic-release generated — if so, skip)

- [ ] **Step 1: Update `models.md`** — replace any "not yet wired" phrasing; document:
    - `LLM_FALLBACK_CHAIN` format, ordering, circuit-breaker skip, budget-fatal rule, `gen_ai.baselith.serving_provider` span attribute.
    - `LLM_ROUTING_ENABLED` / `LLM_ROUTING_POLICY`, model precedence `pinned > per-call > routed > default`, categories from `TaskCategory`.

- [ ] **Step 2: Update `services.md`** — LLMConfig field table/prose: three new fields + flipped `enable_native_tools` default with the `supports_native_tools` guard note.

- [ ] **Step 3: Full verification**

Run: `ruff check . && python scripts/check_architecture_boundaries.py && python scripts/check_core_resilience_typing.py && mypy core/ 2>&1 | tail -3 && python -m pytest tests/unit -q -m "not slow" 2>&1 | tail -5`
Expected: all green, coverage gate ≥65% holds.

- [ ] **Step 4: Commit**

```bash
git add mkdocs-site/docs/core-modules/models.md mkdocs-site/docs/core-modules/services.md
git commit -m "docs(core): document wired LLM fallback chain and model routing"
```

---

## Self-Review Notes

- Spec coverage: fallback wiring (Tasks 1, 3, 4), routing wiring (Tasks 2, 4, 5), native-tools default (Task 2), docs-sync convention (Task 6). Streaming (`_streaming.py`) and structured (`structured.py`) paths intentionally keep the direct provider call in this change — fallback there needs stream-aware semantics (mid-stream failure) and is deferred; documented in Task 6 docs.
- Type consistency: `run_with_fallback` returns `tuple[str, int, str]` everywhere it's referenced; `_resolve_model(model, task_category=None)` signature matches Tasks 4/5 usage; config field names (`fallback_chain`, `routing_enabled`, `routing_policy`) consistent across Tasks 2–6.
- Known verify-at-execution points (flagged inline): `LoopBudget().snapshot()` constructor shape (Task 3 test), existing default-assertion tests for `enable_native_tools` (Task 2), structured.py model-resolution call site (Task 4 3g), intent-classifier test fixtures (Task 5).
