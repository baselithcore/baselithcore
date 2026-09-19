import os
import sys
import types
from typing import Iterable, Sequence
from unittest.mock import AsyncMock, MagicMock

import pytest

# Set required environment variables for tests before modules load
os.environ["SECRET_KEY"] = (
    "super-secret-key-for-testing-purpose-only-with-at-least-thirty-two-chars"
)
os.environ["ALLOW_ORIGINS"] = '["http://testserver"]'

# === Deterministic LLM posture for the whole suite ===
# The suite used to inherit the developer's own ``.env``: whichever provider,
# key and endpoint happened to be on the machine. That makes a green run mean
# different things on different machines, and — on any host that runs Ollama —
# lets a test that slipped past its mocks perform REAL local inference and pass
# because of it. Pin the posture instead, before any settings class binds:
#
# * a keyless provider, so constructing a service never needs a credential;
# * an endpoint on the discard port, so a call that escapes its mock fails in
#   microseconds against nothing instead of quietly using a real model;
# * no fallback chain, so failover is only ever exercised by tests that ask
#   for it explicitly.
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["LLM_MODEL"] = "llama3.2"
os.environ["LLM_FALLBACK_CHAIN"] = ""
os.environ["LLM_PREFLIGHT"] = "off"
# Deliberately NOT ``OLLAMA_HOST``: it is the last resort inside
# ``api_base_for``, and pinning it would change the very resolution order the
# endpoint tests assert on. The dedicated variable outranks it anyway.
os.environ["LLM_OLLAMA_API_BASE"] = "http://127.0.0.1:9"
os.environ["VISION_PROVIDER"] = "openai"
os.environ["VISION_OLLAMA_HOST"] = "http://127.0.0.1:9"


# Fallback body


sys.modules["selenium"] = MagicMock()
sys.modules["selenium.webdriver"] = MagicMock()
sys.modules["selenium.webdriver.common.by"] = MagicMock()
sys.modules["selenium.webdriver.support.ui"] = MagicMock()
sys.modules["selenium.webdriver.support"] = MagicMock()

mock_psycopg = MagicMock()
# Mock ConnectionPool to return an async-compatible mock for close/open/etc
mock_pool = MagicMock()
# close method is awaited in DAOs
mock_pool.close = AsyncMock()
mock_pool.open = AsyncMock()

# Shared mock cursor used by both sync and async paths.
# Use MagicMock (not AsyncMock) for execute/fetch methods: MagicMock is
# awaitable via __await__ in Python 3.8+, so `await cur.execute(...)` works,
# and synchronous `cur.execute(...)` also works without creating an unawaited
# coroutine (which AsyncMock would produce when called without await).
mock_cursor = MagicMock()
mock_cursor.execute = AsyncMock()
mock_cursor.fetchall = AsyncMock(return_value=[])
mock_cursor.fetchone = AsyncMock(return_value=None)
mock_cursor.rowcount = 0

# Shared mock connection — same reasoning as above for commit/execute
mock_conn = MagicMock()
mock_conn.close = AsyncMock()
mock_conn.execute = AsyncMock()
mock_conn.commit = AsyncMock()

# Cursor context manager — explicit __aenter__/__aexit__ so that
# `async with conn.cursor() as cur` does NOT auto-create an AsyncMock conn,
# which would make `conn.cursor()` return an unawaited coroutine.
mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cursor)
mock_conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
# Keep synchronous enter for sync usage (e.g. auth persistence)
mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

# Connection context manager — explicit __aenter__/__aexit__ so that
# `async with pool.connection() as conn` returns mock_conn (a MagicMock),
# not an AsyncMock whose attribute access creates unawaited coroutines.
mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
# Keep synchronous enter for sync usage
mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

# The global psycopg mock below is what keeps the default unit run fast and
# database-free — but it also makes every test under tests/integration/
# structurally unable to reach a real Postgres, no matter how carefully that
# test probes for one (see tests/integration/test_oauth_keystore.py and
# test_pgvector_integration.py). BASELITH_TEST_REAL_DB is the opt-in escape
# hatch: set it for an integration run to leave the real psycopg/psycopg_pool
# modules untouched; every other mock in this file (selenium, etc.) still
# applies either way. Default behaviour (flag unset) is unchanged.
_REAL_DB = os.environ.get("BASELITH_TEST_REAL_DB", "").strip().lower() in {
    "1",
    "true",
    "yes",
}

# Try to use installed psycopg/psycopg_pool if available to preserve submodules (e.g. types.json)
if not _REAL_DB:
    try:
        import psycopg

        # Patch attributes on the real module
        psycopg.ConnectionPool = MagicMock(return_value=mock_pool)
        psycopg.AsyncConnection = MagicMock(return_value=mock_conn)

        # Handle psycopg_pool
        try:
            import psycopg_pool

            psycopg_pool.ConnectionPool = psycopg.ConnectionPool
            psycopg_pool.AsyncConnectionPool = psycopg.ConnectionPool
        except ImportError:
            sys.modules["psycopg_pool"] = MagicMock()
            sys.modules["psycopg_pool"].ConnectionPool = psycopg.ConnectionPool
            sys.modules["psycopg_pool"].AsyncConnectionPool = psycopg.ConnectionPool

    except ImportError:
        # Fallback to full mock if not installed
        mock_psycopg.ConnectionPool.return_value = mock_pool
        sys.modules["psycopg"] = mock_psycopg
        sys.modules["psycopg_pool"] = MagicMock()
        sys.modules["psycopg_pool"].ConnectionPool = mock_psycopg.ConnectionPool
        sys.modules["psycopg_pool"].AsyncConnectionPool = mock_psycopg.ConnectionPool

# Mock internal service to avoid circular imports and global instantiation -> REMOVED to allow integration tests
# mock_service_module = MagicMock()
# sys.modules["app.chat.service"] = mock_service_module


class _DummyEmbedder:
    def encode(self, queries: Sequence[str]) -> Iterable[Sequence[float]]:
        return [[0.1] for _ in queries]


class _DummyHistoryManager:
    def load(self, conversation_id: str | None):
        return ((), "")


class DummyService:
    INITIAL_SEARCH_K = 2
    FINAL_TOP_K = 2

    def __init__(self) -> None:
        self.embedder = _DummyEmbedder()
        self.history_manager = _DummyHistoryManager()
        self.reranker = object()
        self.rerank_cache = None
        self.response_cache = None
        self.newline = "\n"
        self.double_newline = "\n\n"
        self.section_separator = "\n---\n"
        self.project_planner = None

    def _finalize_answer_state(self, state, answer: str) -> str:
        return f"{answer}|finalized"


@pytest.fixture(autouse=True)
def _no_real_local_inference(request):
    """Fail loudly instead of quietly running a real local model.

    The endpoint pinned above already points at nothing, but a developer host
    with ``OLLAMA_HOST`` exported, or a test that builds its own client, can
    still reach a real server — and a test that silently performs local
    inference passes for the wrong reason, slowly, and only on the machines
    that have the model. This makes that a named failure.

    Opt out with ``@pytest.mark.real_llm`` for a test that means it.

    Patches by hand rather than through ``monkeypatch``: requesting that
    fixture from an autouse one instantiates it earlier than the test's own
    use of it, which moves its undo to after the asyncio runner tears the event
    loop down. A test that patched ``time.monotonic`` with a finite
    ``side_effect`` then had the loop's own clock call consume an exhausted
    mock, and failed in teardown for a reason that had nothing to do with it.
    """
    if request.node.get_closest_marker("real_llm"):
        yield
        return
    try:
        import ollama
    except ImportError:
        yield
        return

    def _refuse(*_args, **_kwargs):
        raise AssertionError(
            "a real Ollama client was constructed in a unit test: patch the "
            "provider, or mark the test with @pytest.mark.real_llm"
        )

    original = getattr(ollama, "AsyncClient", None)
    ollama.AsyncClient = _refuse
    try:
        yield
    finally:
        if original is not None:
            ollama.AsyncClient = original


@pytest.fixture(autouse=True)
def silence_telemetry(monkeypatch):
    """Placeholder fixture for telemetry silencing (not needed for core tests)."""
    yield


@pytest.fixture(autouse=True)
def setup_tenant_context():
    """Set a default tenant context for all tests to satisfy strict isolation."""
    from core.context import reset_tenant_context, set_tenant_context

    token = set_tenant_context("default")
    yield
    reset_tenant_context(token)


@pytest.fixture(autouse=True)
def reset_user_context_between_tests():
    """Clear the user context around every test so a leaked id can't bleed across.

    Unlike the tenant context there is no default user; some plugin tests (e.g.
    ``auth``) call ``set_user_context()`` without capturing the reset token, so
    the bound id would otherwise survive into later tests that assert an
    unauthenticated (``None``) context.
    """
    from core import context

    context._user_context.set(None)
    yield
    context._user_context.set(None)


@pytest.fixture(autouse=True)
def _restore_isolated_env_toggles():
    """Restore env toggles that plugin bootstraps or tests clobber process-wide.

    Some plugin bootstraps and tests pin ``POSTGRES_ENABLED`` / ``APP_DOMAIN`` /
    ``AUTH_REQUIRED`` directly in ``os.environ`` without restoring them, so the
    change bleeds into every later test (e.g. flipping ``POSTGRES_ENABLED`` to
    ``"false"`` makes a fresh ``StorageConfig`` report Postgres disabled and
    unrelated DB tests fail). Snapshot and restore those keys around each test
    so the bleed cannot cross test boundaries.
    """
    keys = ("POSTGRES_ENABLED", "APP_DOMAIN", "AUTH_REQUIRED")
    saved = {key: os.environ.get(key) for key in keys}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def _reset_assumed_production_posture():
    """create_app() arms a process-global hardened posture when auth is on and
    no environment is declared; reset it around every test so the flag cannot
    bleed across the randomized suite."""
    from core.utils import runtime_env

    runtime_env.reset_assumed_production()
    yield
    runtime_env.reset_assumed_production()


@pytest.fixture(autouse=True)
async def cleanup_global_state_between_tests():
    """Reset global registries and event bus between tests to prevent cross-test pollution."""
    yield
    # Cleanup after each test
    try:
        from core.di import ServiceRegistry, reset_lazy_registry
        from core.events import reset_event_bus
        from core.events.listener import EventListener

        # Close and reset the global LLM service, if one was ever built.
        #
        # Deliberately NOT ``get_llm_service()``: that CREATES the singleton on
        # demand, so a teardown asking "is there anything to close" built a
        # provider client for the sole purpose of closing it — in a test that
        # never touched the LLM at all. Two things follow from that, both of
        # them seen: constructing it can fail for reasons belonging to no test
        # in particular, and awaiting its close consumes the event loop's clock,
        # which a test that patched ``time.monotonic`` with a finite
        # side_effect has already spent. Read the singleton instead.
        try:
            import asyncio

            from core.services.llm import runtime as _llm_runtime

            svc = _llm_runtime._default_service
            if svc is not None:
                try:
                    await asyncio.wait_for(svc.close(), timeout=1.0)
                except BaseException:
                    pass
            _llm_runtime.reset_llm_service()
        except BaseException:
            pass

        reset_event_bus()
        EventListener._instance = None
        ServiceRegistry.clear()
        reset_lazy_registry()
    except (ImportError, Exception):
        pass

    # Clear the process-global ThoughtCache. ToT evaluation now routes through
    # this shared LRU/TTL cache, so stale entries from a prior test could
    # otherwise satisfy a later test's evaluation and skew LLM call counts.
    try:
        from core.reasoning.tot import cache as _tot_cache

        if _tot_cache._global_thought_cache is not None:
            _tot_cache._global_thought_cache.clear()
    except (ImportError, Exception):
        pass

    # Clear the process-global vision API-key resolver registry. Plugins
    # (e.g. baselithbot) register a resolver in ``initialize`` and only drop it
    # in ``shutdown``; tests that load/init a plugin without a paired shutdown
    # would otherwise leak a mock-backed resolver into VisionService and make
    # unrelated vision tests resolve bogus keys.
    try:
        from core.services.vision import service as _vision_service

        _vision_service._key_resolvers.clear()
    except (ImportError, Exception):
        pass


@pytest.fixture
def dummy_service():
    return DummyService()


@pytest.fixture
def make_state():
    from core.models.chat.chat import ChatRequest

    from core.chat.agent_state import AgentState

    def _make(query: str) -> AgentState:
        return AgentState(request=ChatRequest(query=query))

    return _make


@pytest.fixture
def doc_hit():
    return types.SimpleNamespace(payload={"document_id": "doc"}, id="doc")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Update README.md with the number of passed tests."""
    import re
    from pathlib import Path

    passed = len(terminalreporter.stats.get("passed", []))

    readme_path = Path(config.rootdir) / "README.md"
    if not readme_path.exists():
        return

    content = readme_path.read_text(encoding="utf-8")

    # Regex to find the pytest badge
    # Pattern: [![Tests: ... passed](https://img.shields.io/badge/Tests-..._passed-brightgreen.svg?style=for-the-badge)](tests/)
    badge_re = re.compile(
        r"\[\!\[Tests: \d+ passed\]\(https://img\.shields\.io/badge/Tests-\d+_passed-brightgreen\.svg\?style=for-the-badge\)\]\(tests/\)"
    )

    new_badge = f"[![Tests: {passed} passed](https://img.shields.io/badge/Tests-{passed}_passed-brightgreen.svg?style=for-the-badge)](tests/)"

    if badge_re.search(content):
        new_content = badge_re.sub(new_badge, content)
        if new_content != content:
            readme_path.write_text(new_content, encoding="utf-8")
