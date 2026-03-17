import pytest
from unittest.mock import patch, ANY, MagicMock
from core.graph.core import GraphDb


@pytest.fixture
def mock_dependencies():
    with (
        patch("core.graph.core.Redis") as MockRedis,
        patch("core.graph.core.create_redis_client") as mock_create_redis,
        patch("core.graph.core.RedisTTLCache") as MockRedisCache,
        patch("core.graph.core.TTLCache") as MockTTLCache,
        patch("core.graph.core.get_storage_config") as mock_get_config,
        patch("core.graph.core.query_builder") as mock_qb,
        patch("core.graph.core.operations") as mock_ops,
        patch("core.graph.core.linking") as mock_linking,
        patch("core.graph.core.retrieval") as mock_retrieval,
        patch("core.context.get_current_tenant_id", return_value="default"),
    ):
        # Setup Redis Client
        mock_client_instance = MockRedis.from_url.return_value
        mock_client_instance.execute_command.return_value = []

        # Setup Cache defaults (Miss by default)
        MockTTLCache.return_value.get.return_value = None
        MockRedisCache.return_value.get.return_value = None

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
            "create_redis": mock_create_redis,
            "RedisCache": MockRedisCache,
            "TTLCache": MockTTLCache,
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


def test_init_enabled_memory_cache(mock_dependencies):
    """Test enabled with in-memory cache (default backend scenario)."""
    mock_dependencies["mock_config"].cache_backend = "memory"
    g = GraphDb(enabled=True)
    g._ensure_cache_initialized()
    assert g.is_enabled()
    mock_dependencies["TTLCache"].assert_called_once()
    mock_dependencies["RedisCache"].assert_not_called()


def test_init_enabled_redis_cache(mock_dependencies):
    """Test enabled with redis cache."""
    mock_dependencies["mock_config"].cache_backend = "redis"
    g = GraphDb(enabled=True)
    g._ensure_cache_initialized()
    mock_dependencies["RedisCache"].assert_called_once()


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
    """Test read-only query returns cached result."""
    mock_dependencies["mock_config"].cache_backend = "memory"
    g = GraphDb(enabled=True)
    g._ensure_cache_initialized()
    # Setup cache hit
    g._cache.get.return_value = ["cached_result"]

    res = g.query("MATCH (n) RETURN n")  # Read-only

    assert res == ["cached_result"]
    g._cache.get.assert_called_once()
    mock_dependencies["redis_client"].execute_command.assert_not_called()


def test_query_cache_miss_write(mock_dependencies):
    """Test write query bypassed cache."""
    mock_dependencies["mock_config"].cache_backend = "memory"
    g = GraphDb(enabled=True)

    g.query("CREATE (n)")  # Write

    g._cache.get.assert_not_called()
    mock_dependencies["redis_client"].execute_command.assert_called()


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
