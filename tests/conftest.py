import os
import sys
import types
import pytest
from typing import Sequence, Iterable
from unittest.mock import MagicMock, AsyncMock

# Set required environment variables for tests before modules load
os.environ["SECRET_KEY"] = (
    "super-secret-key-for-testing-purpose-only-with-at-least-thirty-two-chars"
)


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

# Try to use installed psycopg/psycopg_pool if available to preserve submodules (e.g. types.json)
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
def silence_telemetry(monkeypatch):
    """Placeholder fixture for telemetry silencing (not needed for core tests)."""
    yield


@pytest.fixture(autouse=True)
def setup_tenant_context():
    """Set a default tenant context for all tests to satisfy strict isolation."""
    from core.context import set_tenant_context, reset_tenant_context

    token = set_tenant_context("default")
    yield
    reset_tenant_context(token)


@pytest.fixture(autouse=True)
async def cleanup_global_state_between_tests():
    """Reset global registries and event bus between tests to prevent cross-test pollution."""
    yield
    # Cleanup after each test
    try:
        from core.events import reset_event_bus
        from core.events.listener import EventListener
        from core.di import ServiceRegistry, reset_lazy_registry

        # Close and reset global LLM service
        try:
            from core.services.llm.service import get_llm_service, reset_llm_service
            import asyncio

            # Use a short timeout to prevent hanging
            svc = get_llm_service()
            if svc:
                try:
                    await asyncio.wait_for(svc.close(), timeout=1.0)
                except Exception:
                    pass
            reset_llm_service()
        except ImportError:
            pass

        reset_event_bus()
        EventListener._instance = None
        ServiceRegistry.clear()
        reset_lazy_registry()
    except (ImportError, Exception):
        pass


@pytest.fixture
def dummy_service():
    return DummyService()


@pytest.fixture
def make_state():
    from core.chat.agent_state import AgentState
    from core.models.chat.chat import ChatRequest

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
