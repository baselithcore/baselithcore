from unittest.mock import ANY, MagicMock, patch

import pytest

from core.graph._cache import SyncTTLCache
from core.graph.core import GraphDb


@pytest.fixture
def mock_dependencies():
    with (
        patch("core.graph.core.Redis") as MockRedis,
        patch("core.graph.core.create_sync_redis_client") as mock_sync_redis,
        # The cache is a real SyncTTLCache, not a mock. Patching it with a
        # MagicMock is what let the async-cache-from-a-sync-method bug live
        # here undetected: a MagicMock's `get` is synchronous and truthy, so
        # these tests passed against code that handed callers a coroutine.
        patch("core.graph.core.get_storage_config") as mock_get_config,
        patch("core.graph.core.query_builder") as mock_qb,
        patch("core.graph.core.operations") as mock_ops,
        patch("core.graph.core.linking") as mock_linking,
        patch("core.graph.core.retrieval") as mock_retrieval,
        patch("core.context.get_current_tenant_id", return_value="default"),
    ):
        # Setup Redis Client. The graph connection is built through the shared
        # bounded sync pool (``create_sync_redis_client``), not ``Redis.from_url``
        # — ``Redis`` stays patched only so the optional-dependency guard in
        # ``_get_client`` sees a non-None symbol.
        mock_client_instance = mock_sync_redis.return_value
        mock_client_instance.execute_command.return_value = []

        # Setup Config Default
        mock_config = MagicMock()
        mock_config.graph_db_enabled = True
        mock_config.graph_db_name = "test_graph"
        mock_config.graph_db_url = "redis://localhost:6379"
        mock_config.graph_db_timeout = 5.0
        mock_config.graph_cache_ttl = 60
        mock_config.cache_backend = "memory"
        mock_config.cache_redis_url = "redis://localhost:6379"
        mock_config.cache_redis_prefix = "test"
        mock_get_config.return_value = mock_config

        yield {
            "Redis": MockRedis,
            "create_sync_redis": mock_sync_redis,
            "get_config": mock_get_config,
            "mock_config": mock_config,
            "qb": mock_qb,
            "ops": mock_ops,
            "linking": mock_linking,
            "retrieval": mock_retrieval,
            "redis_client": mock_client_instance,
        }


def test_init_disabled(mock_dependencies):
    """Test initialization when disabled via args."""
    # Override config to be enabled, but ensure constructor arg takes precedence
    g = GraphDb(enabled=False)
    assert not g.is_enabled()
    assert g._cache is None


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_the_query_cache_is_in_process_whatever_the_backend(mock_dependencies, backend):
    """`cache_backend` no longer selects the graph's query cache.

    It used to pick between the two ASYNC caches, both of which `query` then
    called without awaiting. The sync surface gets a sync cache; the setting
    still governs every other cache in the framework.
    """
    mock_dependencies["mock_config"].cache_backend = backend
    g = GraphDb(enabled=True)
    g._ensure_cache_initialized()

    assert g.is_enabled()
    assert isinstance(g._cache, SyncTTLCache)


def test_ping_success(mock_dependencies):
    """Test ping returns True when redis is alive."""
    g = GraphDb(enabled=True)
    assert g.ping() is True
    mock_dependencies["redis_client"].ping.assert_called_once()


def test_ping_failure(mock_dependencies):
    """Test ping returns False on exception."""
    g = GraphDb(enabled=True)
    mock_dependencies["redis_client"].ping.side_effect = Exception("Connection Error")
    assert g.ping() is False


def test_query_execution(mock_dependencies):
    """Test basic query execution flow."""
    g = GraphDb(enabled=True)
    mock_dependencies["qb"].build_query.return_value = "MATCH (n) RETURN n"
    mock_dependencies["redis_client"].execute_command.return_value = [["res"]]

    res = g.query("MATCH (n) RETURN n", {"p": 1})

    assert res == [["res"]]
    # Cost tracking removed
    mock_dependencies["qb"].build_query.assert_called_with(
        "MATCH (n) RETURN n", {"p": 1, "tenant_id": "default"}
    )
    mock_dependencies["redis_client"].execute_command.assert_called_with(
        "GRAPH.QUERY", ANY, "MATCH (n) RETURN n", "--compact"
    )


def test_query_cache_hit(mock_dependencies):
    """A read-only query is served from the cache the second time."""
    g = GraphDb(enabled=True)
    g._ensure_cache_initialized()
    g._cache.set("seeded", ["cached_result"])
    mock_dependencies["redis_client"].execute_command.return_value = ["from_backend"]

    first = g.query("MATCH (n) RETURN n")
    second = g.query("MATCH (n) RETURN n")

    assert first == ["from_backend"]
    assert second == ["from_backend"]
    assert mock_dependencies["redis_client"].execute_command.call_count == 1


def test_query_cache_key_is_tenant_scoped(mock_dependencies):
    """Read-only cache key must include tenant_id so two tenants running the
    identical query never collide on the same cache entry (cross-tenant leak).
    """
    mock_dependencies["mock_config"].cache_backend = "memory"
    g = GraphDb()
    g._ensure_cache_initialized()

    seen: list[str] = []
    real_get = g._cache.get
    g._cache.get = lambda key: seen.append(key) or real_get(key)  # type: ignore[method-assign]

    with patch("core.context.get_current_tenant_id", return_value="tenant_a"):
        g.query("MATCH (n) RETURN n", {"id": "x"})
    with patch("core.context.get_current_tenant_id", return_value="tenant_b"):
        g.query("MATCH (n) RETURN n", {"id": "x"})

    key_a, key_b = seen
    assert key_a != key_b
    assert "tenant_a" in key_a
    assert "tenant_b" in key_b


def test_query_cache_miss_write(mock_dependencies):
    """A write is never served from, nor stored in, the cache."""
    g = GraphDb(enabled=True)
    g._ensure_cache_initialized()

    g.query("CREATE (n)")
    g.query("CREATE (n)")

    assert len(g._cache) == 0
    assert mock_dependencies["redis_client"].execute_command.call_count == 2


def test_delegated_operations(mock_dependencies):
    """Test that methods delegate to specialized modules."""
    g = GraphDb(enabled=True)

    g.get_node("id1")
    mock_dependencies["ops"].get_node.assert_called_once()

    g.upsert_node("id1")
    mock_dependencies["ops"].upsert_node.assert_called_once()

    g.get_document_subgraph("doc1")
    mock_dependencies["retrieval"].get_subgraph_for_node.assert_called_once()


def test_create_constraints(mock_dependencies):
    """Test constraint creation commands."""
    g = GraphDb(enabled=True)
    g.create_constraints()

    assert (
        mock_dependencies["redis_client"].execute_command.call_count >= 2
    )  # 1 label * 2 commands (index + constraint)


def test_lazy_connection_error(mock_dependencies):
    """Test error if redis package missing (simulated by None)."""
    with patch("core.graph.core.Redis", None):
        g = GraphDb(enabled=True)
        with pytest.raises(RuntimeError, match="requires the redis package"):
            g._get_client()


def test_close(mock_dependencies):
    """Test client close."""
    g = GraphDb(enabled=True)
    g.query("MATCH (n) RETURN n")  # Ensure client created
    g.close()
    mock_dependencies["redis_client"].close.assert_called_once()
    assert g._client is None
