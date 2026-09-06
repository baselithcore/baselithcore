---
title: Testing
description: Complete guide to testing BaselithCore and its plugins
---

Complete guide to testing BaselithCore.

---

## Test Structure

```text
tests/
├── conftest.py            # Shared fixtures: tenant context, global-state reset
├── unit/                  # Fast, isolated tests; LLM auto-mocked (unit/conftest.py)
│   ├── conftest.py
│   ├── core/              # One subdirectory per core module
│   │   ├── auth/
│   │   ├── memory/
│   │   ├── orchestration/
│   │   ├── task_queue/
│   │   ├── utils/
│   │   └── ...
│   ├── mcp/
│   └── plugins_tests/     # Router tests for the api_routers plugin
├── integration/           # Cross-module wiring: orchestrator, plugin loading, pgvector
├── contracts/             # OpenAPI conformance (schemathesis) + service protocols
├── chaos/                 # Fault-injection resilience tests (marker: chaos)
├── golden/                # Real Agent loop driven by recorded LLM cassettes
├── core/                  # Plugin-system and memory tests
├── plugins/               # Per-plugin suites (api_routers, baselithbot)
└── load/                  # Locust profile — not part of the pytest suite
```

Unit tests mirror the `core/` tree: a test for `core/utils/tokens.py` lives in
`tests/unit/core/utils/test_tokens.py`. There is no `e2e/` directory — HTTP
tests drive the ASGI app in-process (see [HTTP tests](#http-tests)).

---

## Setup

### Dependencies

```bash
pip install -e ".[test]"
```

CI installs the **locked** set instead (`uv export --frozen --extra test`), so
a green local run and a green CI run exercise the same dependency versions —
see [CI/CD Integration](#cicd-integration).

### Configuration

The suite is configured in `pytest.ini` at the repository root:

```ini title="pytest.ini"
[pytest]
minversion = 6.0
testpaths = tests
python_files = test_*.py *_test.py
python_classes = Test*
python_functions = test_*
addopts =
    -v
    --strict-markers
    --tb=short
    --cov=core
    --cov-report=term-missing
    --cov-report=html:htmlcov
    --cov-fail-under=75
timeout = 120
timeout_method = thread
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    integration: marks tests as integration tests
    unit: marks tests as unit tests
    contract: marks tests as contract tests
    chaos: marks fault-injection resilience tests (deselect with '-m "not chaos"')
asyncio_mode = auto
```

What that means in practice:

- **`asyncio_mode = auto`** — any `async def test_*` (and any `async def`
  fixture) runs on the event loop without a marker.
- **`--strict-markers`** — a marker that is not declared above fails
  collection; add new markers to `pytest.ini` first.
- **Coverage is always on**, measured over `core/` only, with a **75 %
  branch** gate (`[tool.coverage.run] branch = true` in `pyproject.toml`).
- **`timeout = 120`** — a hung test fails itself instead of stalling the job.

---

## Unit Tests

### Core Services

Unit tests construct the component under test with explicit fakes — no
provider, no network. `AgentMemory` takes its persistence provider and
embedder as constructor arguments, so both are plain mocks (mirrors
`tests/unit/core/memory/test_manager.py`):

```python title="tests/unit/core/memory/test_manager.py (excerpt)"
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.memory.manager import AgentMemory
from core.memory.types import MemoryType


@pytest.fixture
def mock_embedder():
    embedder = MagicMock()
    embedder.encode = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return embedder


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    provider.add = AsyncMock()
    provider.search = AsyncMock(return_value=[])
    return provider


async def test_add_memory_persists_long_term(mock_embedder, mock_provider):
    manager = AgentMemory(provider=mock_provider, embedder=mock_embedder)

    item = await manager.add_memory("test content", MemoryType.LONG_TERM)

    assert item.content == "test content"
    mock_provider.add.assert_called_once()


async def test_recall_from_working_memory(mock_embedder):
    manager = AgentMemory(embedder=mock_embedder)
    # A, B, then the query vector (matches A)
    mock_embedder.encode.side_effect = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]

    await manager.add_memory("A", MemoryType.SHORT_TERM)
    await manager.add_memory("B", MemoryType.SHORT_TERM)

    results = await manager.recall("query", limit=1)

    assert [r.content for r in results] == ["A"]
```

### Plugin Tests

A plugin is a `Plugin` subclass with a `metadata` property and async
`initialize` / `shutdown` hooks. The registry refuses a plugin that has not
been initialized (mirrors `tests/core/test_plugin_system.py` and
`tests/integration/test_plugin_loading.py`):

```python title="tests/core/test_plugin_system.py (excerpt)"
from core.plugins import Plugin, PluginMetadata, PluginRegistry


class EchoPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="echo", version="1.0.0", description="Echo")

    async def initialize(self, config=None):
        await super().initialize(config or {})

    async def shutdown(self):
        pass


def test_plugin_metadata():
    plugin = EchoPlugin()

    assert plugin.metadata.name == "echo"
    assert plugin.metadata.dependencies == []


async def test_registry_requires_initialized_plugin():
    registry = PluginRegistry()
    plugin = EchoPlugin()
    await plugin.initialize({})  # register() raises ValueError otherwise

    registry.register(plugin)

    assert registry.get("echo") is plugin
    assert len(registry) == 1
```

---

## Integration Tests

### Plugin Loading

`PluginLoader(plugins_dir, registry)` discovers plugin directories under
`plugins_dir`, imports each one, and registers what it finds. Build a
throwaway plugin under `tmp_path` rather than loading the real `plugins/`
tree (mirrors `tests/unit/core/plugins/test_loader_env_security.py`):

```python title="tests/unit/core/plugins/test_loader_env_security.py (excerpt)"
from pathlib import Path

import pytest

from core.plugins import PluginLoader, PluginRegistry


@pytest.fixture
def plugins_root(tmp_path: Path) -> Path:
    root = tmp_path / "plugins"
    root.mkdir()
    return root


def _make_plugin(root: Path, name: str) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir()
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\n", encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text("x = 1\n", encoding="utf-8")
    return plugin_dir


async def test_load_plugin_without_plugin_class_returns_none(plugins_root: Path):
    plugin_dir = _make_plugin(plugins_root, "nsplugin")
    registry = PluginRegistry()
    loader = PluginLoader(plugins_root, registry)

    # The module defines no Plugin subclass, so nothing is registered
    plugin = await loader.load_plugin(plugin_dir, initialize=False)

    assert plugin is None
    assert registry.get("nsplugin") is None
```

`loader.load_all_plugins()` runs discovery plus dependency ordering over the
whole directory and returns the number of plugins loaded. Once a plugin is
registered, `registry.get(name)` returns the instance and
`registry.get_flow_handler(intent_name)` the handler it contributed for an
intent.

### Flow Execution

The orchestrator takes every collaborator through its constructor, so an
integration test can substitute the intent classifier and the flow handler
while exercising the real pipeline — guardrails, budget, checkpointing
(mirrors `tests/integration/test_orchestrator_patterns.py`):

```python title="tests/integration/test_orchestrator_patterns.py (excerpt)"
from unittest.mock import AsyncMock, MagicMock

from core.orchestration import Orchestrator


async def test_orchestrator_routes_to_handler():
    # IntentClassifier.classify is async and returns the intent name
    classifier = MagicMock()
    classifier.classify = AsyncMock(return_value="chat")

    orchestrator = Orchestrator(intent_classifier=classifier)

    handler = AsyncMock()
    handler.handle.return_value = {"content": "Handled", "type": "final"}
    orchestrator._flow_handlers = {"chat": handler}

    await orchestrator.process(
        "Hello agent", context={"user_id": "user1", "session_id": "sess1"}
    )

    handler.handle.assert_called_once()
```

---

## HTTP Tests

Drive the ASGI app in-process with `httpx.ASGITransport` — no server, no
port. Mount only the router under test on a bare `FastAPI()` app (mirrors
`tests/unit/plugins_tests/test_runs_router.py`); the `/health` liveness probe
below is unauthenticated and has no dependency checks:

```python
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from plugins.api_routers.status import router as status_router


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(status_router)
    return app


async def test_health_liveness(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

For a route that sits behind `Depends(require_admin)`, override the dependency
on the app (`app.dependency_overrides[...]`) instead of minting credentials.
When the whole middleware stack matters — security headers, CSRF, request-size
limits — build the real application with `core.api.factory.create_app()`
(as `tests/unit/core/api/test_env_posture.py` does) and pass it to the same
transport.

---

## Common Fixtures

Two `conftest.py` files supply the shared fixtures; none of them creates
database tables — the relational schema is owned by Alembic (`alembic.ini`,
`migrations/`), unit tests mock their storage, and the integration tests that
need a live backend (`tests/integration/test_pgvector_integration.py`) use the
Postgres and Redis services the CI job provides.

**`tests/conftest.py`** (every test):

| Fixture | Scope | What it does |
| ------- | ----- | ------------ |
| `setup_tenant_context` | autouse | Sets the tenant context to `default` so strict tenant isolation is satisfied |
| `_reset_assumed_production_posture` | autouse | Clears the process-global "assume production" flag `create_app()` may arm |
| `cleanup_global_state_between_tests` | autouse, async | After each test: closes and resets the global LLM service, event bus, `ServiceRegistry`, lazy registry, ToT thought cache and vision key resolvers |
| `dummy_service` | function | A minimal chat-service stand-in (`DummyService`) for handler tests |
| `make_state` | function | Factory: `make_state(query)` returns an `AgentState` wrapping a `ChatRequest` |

**`tests/unit/conftest.py`** (unit tests only):

| Fixture | Scope | What it does |
| ------- | ----- | ------------ |
| `mock_llm_service` | autouse | Patches `core.services.llm.get_llm_service` with a `MagicMock` whose `generate_response` is an `AsyncMock` — see [Mocking LLM Services](#mocking-llm-services) |
| `reset_circuit_breakers` | autouse | Resets every global circuit breaker to `CLOSED` so a tripped breaker cannot leak into the next test |

When a component resolves collaborators through the DI container, register a
fake instance directly (`core/di/container.py`):

```python
from core.di import DependencyContainer
from core.interfaces import LLMServiceProtocol


class FakeLLM:
    async def generate_response(self, prompt: str, **kwargs) -> str:
        return f"Mock response to: {prompt}"


def test_container_resolves_registered_instance():
    container = DependencyContainer()
    container.register_instance(LLMServiceProtocol, FakeLLM())

    assert isinstance(container.resolve(LLMServiceProtocol), FakeLLM)
```

`container.register(interface, factory, lifetime=ServiceLifetime.SINGLETON)`
is the factory form; `TRANSIENT` and `SCOPED` lifetimes are covered in
`tests/unit/core/di/test_di_container.py`.

---

## Coverage

### Run with Coverage

`--cov=core` is already in `addopts`, so a plain `pytest` measures coverage and
enforces the gate:

```bash
# Full suite, gate enforced (75 % branch coverage over core/)
pytest

# Only unit tests
pytest tests/unit/

# Add plugin coverage to the report (not part of the gate)
pytest --cov=plugins --cov-report=html

# Raise the bar locally
pytest --cov-fail-under=80
```

!!! note "Suite hygiene defaults"
    The suite measures **branch** coverage (`[tool.coverage.run] branch=true`),
    runs tests in **random order** (`pytest-randomly` — reproduce a failing
    order with `--randomly-seed=<seed>` from the run header), and applies a
    **120s per-test timeout** (`pytest-timeout`), so a hung async test fails
    itself instead of stalling the job. Property-based tests (`hypothesis`)
    cover `core/resilience`; `tests/contracts/test_openapi_conformance.py`
    (schemathesis) validates live responses against the exported OpenAPI spec.

### Report

```bash
# HTML Report (written by every run)
open htmlcov/index.html

# Terminal Report with missing lines
pytest --cov-report=term-missing
```

---

## Coverage Requirements

There is a single enforced gate: **`--cov-fail-under=75`**, measured as
**branch** coverage over `core/` (`pytest.ini`). Plugins, scripts and
templates are reported when you ask for them but never gated.

The number is a **ratchet**: it was set to the measured value minus a small
margin and only ever moves up. When the suite grows past the gate by a
comfortable distance, raise the value in `pytest.ini` in the same change —
never lower it to make a red build pass; add the missing tests instead.

---

## Testing Best Practices

### Mocking LLM Services

Always mock LLM services in tests to avoid external dependencies and ensure test reliability.

#### Auto-Mocking via Conftest

Every test under `tests/unit/` gets a mocked LLM service automatically:

```python title="tests/unit/conftest.py"
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_llm_service():
    """
    Mock LLMService for all unit tests to prevent real API calls and configuration errors.
    This fixture automatically patches `core.services.llm.get_llm_service`.
    """
    with patch("core.services.llm.get_llm_service") as mock_get:
        mock_service = MagicMock()
        # generate_response is async on the real LLMService — mock it as such
        # so callers that `await` it get a value, not an un-awaitable Mock.
        mock_service.generate_response = AsyncMock(return_value="Mocked LLM Response")
        # Mock streaming response
        mock_service.generate_response_stream.return_value = iter(
            ["Mock", "ed", " stream"]
        )

        mock_get.return_value = mock_service
        yield mock_get
```

The fixture yields the patched **getter**; the service is
`mock_llm_service.return_value`. It only intercepts code that resolves the
service lazily through `core.services.llm.get_llm_service` — integration tests
(`tests/integration/`) are outside its scope and build their own mocks.

#### Custom LLM Mocking

Components that accept an `llm_service` take the mock through the constructor,
which is both explicit and independent of the patch above:

```python
from unittest.mock import AsyncMock, MagicMock

from core.reasoning import ChainOfThought


async def test_chain_of_thought_uses_injected_llm():
    llm = MagicMock()
    llm.generate_response = AsyncMock(
        return_value="1. Multiply six by seven.\n2. Check the result.\nAnswer: 42"
    )

    cot = ChainOfThought(llm_service=llm)
    answer, steps = await cot.reason("What is 6 * 7?")

    llm.generate_response.assert_awaited_once()
    assert answer == "42"
    assert len(steps) == 2
```

### Golden Trajectories (Recorded LLM Cassettes)

An `AsyncMock` proves the loop *calls* the service; it cannot prove the loop
sent the right thing. `tests/golden/` drives the real `core.agent.Agent` with a
**cassette** — an ordered list of provider turns in
`tests/golden/cassettes/<name>.json` — and every turn asserts what the loop
sent before answering:

```json
{
  "expect": {
    "prompt_contains": ["[lookup_capital] -> Rome", "answer without calling more tools"],
    "tools": ["lookup_capital"],
    "response_format": null
  },
  "result": {"text": "The capital of Italy is Rome.", "tool_calls": [], "stop_reason": "end_turn"}
}
```

`expect.tools` is the set of tools the loop must offer, `prompt_contains` the
fragments the assembled prompt must carry (the previous tool result, the
retry wording, the validation error), `response_format` the strict-schema
name. Any drift raises `CassetteMismatch` with the full prompt; a loop that
finishes without playing every recorded turn fails at teardown.

```python
@pytest.mark.asyncio
async def test_tool_loop_matches_cassette(golden_llm):
    svc = golden_llm("agent_tool_loop")
    agent = Agent(tools=[lookup_capital], llm_service=svc)
    result = await agent.run("What is the capital of Italy?")
    assert result.tool_calls_made == ["lookup_capital"]
```

To capture a new cassette from a live provider, run the test once with
`BASELITH_GOLDEN_RECORD=1` (credentials configured): `RecordingLLMService`
wraps the real `LLMService`, writes the turns with the offered tools and the
schema name filled in, and leaves `prompt_contains` for you to curate from the
recorded prompt. Cassettes are replayed in CI with no keys, cost or network.

### Async Testing

With `asyncio_mode = auto` a plain `async def` test is enough; the
`@pytest.mark.asyncio` marker is accepted but redundant:

```python
# ✅ Correct async testing
async def test_async_function():
    """Test async operation properly."""
    result = await my_async_function()
    assert result.success


# ❌ Incorrect - don't use asyncio.run
def test_async_wrong():
    result = asyncio.run(my_async_function())  # NO! Use async def
```

#### Async Fixtures

Async fixtures work the same way — `tests/conftest.py` itself declares
`cleanup_global_state_between_tests` as an autouse `async def` fixture:

```python
@pytest.fixture
async def async_setup():
    """Async fixture for test setup."""
    manager = await AsyncManager.create()
    yield manager
    await manager.cleanup()


async def test_with_async_fixture(async_setup):
    """Test using async fixture."""
    result = await async_setup.process()
    assert result is not None
```

### Redis and RQ Mocking

Follow the established pattern for mocking Redis and RQ job queues. Note the
autouse tracker stub: `TaskScheduler.enqueue`/`enqueue_at` record the job's
initial status through a **real** Redis connection unless
`get_task_tracker` is patched:

```python title="tests/unit/core/task_queue/test_scheduler.py (excerpt)"
from unittest.mock import MagicMock, patch

import pytest
from rq.job import Job, JobStatus

from core.task_queue.scheduler import TaskScheduler, schedule_task


@pytest.fixture
def mock_queue():
    queue = MagicMock()
    job = MagicMock(spec=Job)
    job.id = "test-job-id"
    job.get_status.return_value = JobStatus.QUEUED
    queue.enqueue.return_value = job
    queue.enqueue_at.return_value = job
    return queue


@pytest.fixture
def mock_get_queue(mock_queue):
    with patch("core.task_queue.scheduler.get_queue", return_value=mock_queue) as mock:
        yield mock


@pytest.fixture(autouse=True)
def mock_task_tracker():
    with patch("core.task_queue.scheduler.get_task_tracker") as mock:
        yield mock.return_value


def dummy_task(x, y):
    return x + y


def test_schedule_task_delays_via_enqueue_in(mock_get_queue, mock_task_tracker):
    with patch.object(TaskScheduler, "enqueue_in") as mock_enqueue_in:
        # schedule_task(func, delay_seconds, *args, queue="default", **kwargs)
        schedule_task(dummy_task, 60, 1, 2)

    mock_enqueue_in.assert_called_once()
```

#### Mocking Redis Client

`RedisTTLCache` takes the client as its first argument, so hand it a mock
whose coroutine methods are `AsyncMock`s (mirrors
`tests/unit/core/cache/test_redis_cache_ndarray.py`):

```python
from unittest.mock import AsyncMock, MagicMock

from core.cache import RedisTTLCache


async def test_set_writes_through_the_client():
    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    client.setex = AsyncMock(return_value=True)

    cache = RedisTTLCache(client, prefix="test", default_ttl=60)
    await cache.set("key", {"value": 1})

    assert client.set.await_count + client.setex.await_count == 1
```

Code that builds its own client goes through
`core.cache.create_redis_client(url)`; `tests/unit/core/cache/test_redis_cache.py`
patches `ConnectionPool` and `Redis` on `core.cache.redis_cache` to keep that
path offline. The URL itself comes from `CACHE_REDIS_URL`
(`core.config.cache.get_redis_cache_config().url`).

### Configuration Mocking

Settings models accept their **environment aliases** as keyword arguments, so
a test can build an explicit config without touching `os.environ` (mirrors
`tests/unit/core/config/test_storage_conninfo_leak.py`):

```python
from unittest.mock import patch

from core.config.storage import StorageConfig


def test_storage_config_from_env_aliases():
    config = StorageConfig(CACHE_REDIS_URL="redis://cache:6379/15")

    assert config.cache_redis_url.endswith("/15")


def test_component_sees_the_override():
    config = StorageConfig(CACHE_REDIS_URL="redis://cache:6379/15")

    # Patch the getter where the code under test looks it up
    with patch("core.config.storage.get_storage_config", return_value=config):
        ...
```

The `get_*_config()` getters are cached singletons: patch the getter at the
import site the code under test actually uses (a component that does
`from core.config import get_task_queue_config` inside a method needs
`patch("core.config.get_task_queue_config", ...)`, as
`tests/unit/core/task_queue/test_scheduler.py` does).

### Test Organization

Organize tests by functionality using classes (mirrors
`tests/unit/core/world_model/test_risk_assessor.py`):

```python
import pytest

from core.world_model.risk_assessor import RiskAssessor
from core.world_model.types import Action, ActionType, RiskLevel


class TestRiskAssessor:
    """Tests for risk assessment functionality."""

    @pytest.fixture
    def assessor(self):
        """Risk assessor instance."""
        return RiskAssessor()

    def test_assess_low_risk_action(self, assessor):
        """Test low-risk action assessment."""
        action = Action(name="query", action_type=ActionType.QUERY)
        result = assessor.assess_action(action)
        assert result["level"] in [RiskLevel.MINIMAL, RiskLevel.LOW]

    def test_assess_high_risk_action(self, assessor):
        """Test high-risk action assessment."""
        action = Action(name="delete", action_type=ActionType.DELETE)
        result = assessor.assess_action(action)
        assert result["level"] in [RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
```

### Parameterized Tests

Use `@pytest.mark.parametrize` for testing multiple scenarios.
`_classify_text` maps the code and CJK ratios of a sample to a
chars-per-token estimate; its first argument is a hash of the sample that only
keys the `lru_cache` (mirrors `tests/unit/core/utils/test_tokens.py`):

```python
import pytest

from core.utils.tokens import _classify_text


@pytest.mark.parametrize(
    ("code_ratio", "cjk_ratio", "expected_chars_per_token"),
    [
        (0.0, 0.0, 4.0),   # prose
        (0.10, 0.0, 3.0),  # code: symbol-dense
        (0.0, 0.5, 1.5),   # CJK: very token-dense
    ],
)
def test_classify_text_ratio(code_ratio, cjk_ratio, expected_chars_per_token):
    """Test text classification with various ratios."""
    result = _classify_text(hash("sample"), code_ratio, cjk_ratio)
    assert result == expected_chars_per_token
```

### Edge Case Testing

Always test edge cases and boundary conditions:

```python
from core.utils.tokens import estimate_tokens


class TestTokenEstimation:
    """Tests for token estimation."""

    def test_empty_string(self):
        """Empty string should return 0 tokens."""
        assert estimate_tokens("") == 0

    def test_very_long_text(self):
        """Very long text should be handled efficiently."""
        text = "word " * 10000  # 10k words
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert tokens < len(text)  # Tokens < characters

    def test_unicode_handling(self):
        """Unicode characters should be counted correctly."""
        text = "Hello  世界"
        tokens = estimate_tokens(text)
        assert tokens > 0
```

---

## General Best Practices

### Isolation

```python
from core.memory.manager import AgentMemory


# ✅ Each test is isolated
@pytest.fixture
def isolated_memory():
    # A fresh AgentMemory() per test starts with empty working memory
    return AgentMemory()


# ❌ Shared state between tests
global_memory = AgentMemory()  # NO!
```

### Async Best Practices

```python
# ✅ Use pytest-asyncio (asyncio_mode = auto)
async def test_async_operation():
    result = await async_function()
    assert result is not None


# ❌ Blocking in async test
def test_blocking():
    result = asyncio.run(async_function())  # NO! Use async def
```

### Mock External Services

```python
# ✅ Mock external services
@pytest.fixture
def mock_http_client(monkeypatch):
    async def mock_get(*args, **kwargs):
        return MockResponse(status_code=200, json={"data": "test"})

    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)


# ❌ Real calls to external services
async def test_real_api():
    response = await httpx.get("https://api.example.com")  # NO! Flaky
```

---

## CI/CD Integration

### Docs ↔ code consistency gate

`CLAUDE.md` says code and docs ship together. Two scripts make the
mechanical half of that rule enforceable:

- `scripts/check_docs_consistency.py` scans every page under
  `mkdocs-site/docs/` and verifies each provable claim against the
  repository: `from core… import` lines in Python fences really import,
  `core/…`/`plugins/…`/`tests/…` paths exist, relative `.md` links resolve,
  `NAME=` lines in env fences and `` `PREFIX_NAME` `` tokens name a real
  setting (aliases and `env_prefix` fields are derived from the config
  classes), `baselith <cmd> <sub>` chains exist in the CLI tree and, with
  `--routes`, every `METHOD /path` is served by the app (feature gates are
  opened while the app is built, so gated routers count too).
- `scripts/check_docs_sync.py` lists the `core/` modules a change touched and
  fails when their documentation page was not edited in the same change. A
  commit whose message carries `[docs-sync: skip]` (with the reason) opts a
  range out explicitly.

Where they run:

| Stage | Command | Scope |
| ----- | ------- | ----- |
| pre-commit hook `docs-consistency` | `python scripts/check_docs_consistency.py --fast` | paths, links, env, CLI |
| CI job `docs_consistency` | `python scripts/check_docs_consistency.py --routes` | everything, imports and routes included |
| CI job `docs_consistency` (pull requests) | `python scripts/check_docs_sync.py origin/<base>` | changed `core/` modules ↔ pages |
| unit suite | `tests/unit/test_docs_consistency.py` | the checker itself, plus the real docs tree (`slow`) |

Tutorial scaffolds (`plugins/my-plugin/…`, `plugins/weather_agent/…`) and
files the reader is told to create are allow-listed in the script; a page
that documents another service's endpoints opts out of one check with an
HTML comment, for example `<!-- docs-consistency: skip routes -->`. When the
gate fires, fix the page or the code — never silence it to make a build
green.

### GitHub Actions

Tests run in the `python_test` job of `.github/workflows/ci.yml` on a Python
3.12 / 3.13 matrix (3.14 advisory), with Postgres 16 and Redis 7 as service
containers. Dependencies come from the lock file, not a fresh resolution:

```yaml title=".github/workflows/ci.yml (python_test, abridged)"
python_test:
  name: Python Tests (${{ matrix.python-version }})
  runs-on: ubuntu-latest
  needs: [architecture_boundaries, type_check, type_check_plugins, type_check_core_resilience, security_scan, package_smoke, evals, red_team, fairness]
  strategy:
    matrix:
      python-version: ['3.12', '3.13']
  services:
    postgres:
      image: postgres:16-alpine
    redis:
      image: redis:7-alpine
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: ${{ matrix.python-version }}
    - name: Install uv
      run: pip install uv==0.12.0
    - name: Run Tests
      run: |
        uv export --frozen --extra test --no-emit-project \
          --format requirements-txt -o /tmp/requirements-test.txt
        uv pip install --system --no-config --require-hashes \
          -r /tmp/requirements-test.txt
        uv pip install --system --no-deps -e .
        pytest -n auto --cov=core --cov-report=xml:coverage.xml --cov-report=term -q
    - name: Upload coverage reports to Codecov
      if: matrix.python-version == '3.12'
      uses: codecov/codecov-action@v4
      with:
        token: ${{ secrets.CODECOV_TOKEN }}
        file: ./coverage.xml
```

The job runs `pytest -n auto` (pytest-xdist) on top of the `pytest.ini`
defaults, so the 75 % branch gate applies in CI exactly as it does locally.

---

## Debugging Tests

```bash
# Run with verbose output
pytest tests/ -v

# Stop at first failure
pytest tests/ -x

# Run specific test
pytest tests/unit/core/memory/test_manager.py::test_add_memory_async

# Show print statements
pytest tests/ -s

# Reproduce a random-order failure
pytest --randomly-seed=<seed>
```

---

## Load Testing

A [Locust](https://locust.io) profile (`tests/load/locustfile.py`) drives the
public request paths — health, chat, feedback — at configurable concurrency
against a **running** instance. It is not part of the unit suite.

```bash
pip install -e ".[load]"

# Interactive web UI at http://localhost:8089
BASELITH_API_KEY=sk-... locust -f tests/load/locustfile.py --host http://localhost:8000

# Headless smoke: 50 users, ramp 10/s, 30s
BASELITH_API_KEY=sk-... locust -f tests/load/locustfile.py \
  --host http://localhost:8000 --headless -u 50 -r 10 -t 30s
```

Task weights (health 5 : chat 10 : feedback 2) approximate a chat-heavy
workload; tune them in the locustfile. See `tests/load/README.md`.

## Chaos / Resilience Testing

Fault-injection tests (`tests/chaos/`, marked `chaos`) verify the framework
**degrades gracefully** by exercising the real resilience primitives — they are
part of the normal suite, no external infrastructure required:

| Scenario | Asserts |
| -------- | ------- |
| Circuit breaker | Trips `OPEN` after the failure threshold, fast-rejects without invoking the callee, recovers via `HALF_OPEN` after the reset timeout |
| Fallback chain | Falls through to the next healthy provider; skips providers whose breaker is open; raises when all fail |
| Retry | Rides out transient failures; raises after exhausting attempts |
| Bulkhead | Caps concurrency at the configured limit under a burst |

```bash
# Run only the chaos tests
pytest -m chaos

# Skip them
pytest -m "not chaos"
```
