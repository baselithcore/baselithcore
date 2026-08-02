"""IndexStateStore behaviour: Redis client lifecycle, state load/save, close."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_redis_state_management(indexing_service):
    mock_redis = AsyncMock()
    # Mock get returning some state
    state_data = json.dumps(
        {"doc_old": {"fingerprint": "fp_old", "metadata": {"m": 1}}}
    )
    mock_redis.get = AsyncMock(return_value=state_data)
    mock_redis.set = AsyncMock(return_value=True)

    with patch.object(
        indexing_service._store, "_get_redis_client", return_value=mock_redis
    ):
        # Load state
        await indexing_service._load_state()
        assert "doc_old" in indexing_service.indexed_documents
        assert indexing_service.indexed_documents["doc_old"].fingerprint == "fp_old"

        # Save state
        await indexing_service._save_state()
        mock_redis.set.assert_called_once()
        args, _ = mock_redis.set.call_args
        saved_state = json.loads(args[1])
        assert "doc_old" in saved_state


@pytest.mark.asyncio
async def test_load_state_error(indexing_service):
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=Exception("Redis down"))

    # Reset state to ensure load is called
    indexing_service._state_loaded = False
    with patch.object(
        indexing_service._store, "_get_redis_client", return_value=mock_redis
    ):
        await indexing_service._load_state()  # Should not raise
        assert indexing_service._state_loaded is True


@pytest.mark.asyncio
async def test_redis_client_caching(indexing_service):
    mock_redis = MagicMock()
    indexing_service._store._redis = mock_redis
    # Hits IndexStateStore._get_redis_client
    assert indexing_service._store._get_redis_client() == mock_redis


@pytest.mark.asyncio
async def test_get_redis_client_success(indexing_service):
    indexing_service._store._redis = None
    mock_redis = MagicMock()
    with (
        patch("core.cache.create_redis_client", return_value=mock_redis),
        patch(
            "core.config.get_storage_config",
            return_value=MagicMock(cache_redis_url="redis://localhost"),
        ),
    ):
        client = indexing_service._store._get_redis_client()
        assert client == mock_redis
        assert indexing_service._store._redis == mock_redis


@pytest.mark.asyncio
async def test_close_redis_error(indexing_service):
    mock_redis = AsyncMock()
    mock_redis.close.side_effect = Exception("Close error")
    indexing_service._store._redis = mock_redis
    await indexing_service.close()
    assert indexing_service._store._redis is None


@pytest.mark.asyncio
async def test_load_state_already_loaded(indexing_service):
    indexing_service._state_loaded = True
    # Hits line 449
    await indexing_service._load_state()


@pytest.mark.asyncio
async def test_load_state_no_redis(indexing_service):
    indexing_service._state_loaded = False
    with patch.object(indexing_service._store, "_get_redis_client", return_value=None):
        await indexing_service._load_state()
        assert indexing_service._state_loaded is True


@pytest.mark.asyncio
async def test_save_state_no_redis(indexing_service):
    with patch.object(indexing_service._store, "_get_redis_client", return_value=None):
        await indexing_service._save_state()


@pytest.mark.asyncio
async def test_save_state_error(indexing_service):
    mock_redis = AsyncMock()
    mock_redis.set.side_effect = Exception("Set error")
    with patch.object(
        indexing_service._store, "_get_redis_client", return_value=mock_redis
    ):
        await indexing_service._save_state()


@pytest.mark.asyncio
async def test_close_redis(indexing_service):
    mock_redis = AsyncMock()
    indexing_service._store._redis = mock_redis
    await indexing_service.close()
    mock_redis.close.assert_called_once()
    assert indexing_service._store._redis is None


@pytest.mark.asyncio
async def test_get_redis_client_no_url(indexing_service):
    mock_storage_config = MagicMock()
    mock_storage_config.cache_redis_url = None
    with patch("core.config.get_storage_config", return_value=mock_storage_config):
        client = indexing_service._store._get_redis_client()
        assert client is None


@pytest.mark.asyncio
async def test_get_redis_client_error(indexing_service):
    with patch("core.config.get_storage_config", side_effect=Exception("Boom")):
        client = indexing_service._store._get_redis_client()
        assert client is None
