"""
Tests for core.realtime.pubsub module.
"""

from unittest.mock import patch, Mock, AsyncMock
import pytest
from core.realtime.pubsub import CHANNEL_PREFIX, PubSubManager

TEST_REDIS_URL = "redis://test:6379/0"


class TestChannelPrefix:
    """Tests for channel prefix constant."""

    def test_channel_prefix_defined(self):
        """Test CHANNEL_PREFIX is defined."""
        assert CHANNEL_PREFIX == "events:"


class TestPubSubManager:
    """Tests for PubSubManager class."""

    @pytest.mark.asyncio
    @patch("core.realtime.pubsub.AsyncRedis")
    async def test_get_redis_async(self, mock_redis_cls):
        """Test get_redis_async returns redis client with correct URL."""
        mock_client = Mock()
        mock_redis_cls.from_url.return_value = mock_client

        manager = PubSubManager(TEST_REDIS_URL)
        result = await manager.get_redis_async()

        mock_redis_cls.from_url.assert_called_once_with(
            TEST_REDIS_URL, decode_responses=True
        )
        assert result == mock_client

    @pytest.mark.asyncio
    @patch("core.realtime.pubsub.AsyncRedis")
    async def test_publish_sends_event(self, mock_redis_cls):
        """Test publish sends event to redis."""
        mock_client = AsyncMock()
        mock_redis_cls.from_url.return_value = mock_client

        manager = PubSubManager(TEST_REDIS_URL)

        mock_event = Mock()
        mock_event.model_dump_json.return_value = '{"type": "test"}'

        await manager.publish("test_channel", mock_event)

        mock_client.publish.assert_called_once()
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.realtime.pubsub.AsyncRedis")
    async def test_publish_handles_error_gracefully(self, mock_redis_cls):
        """Test publish handles errors gracefully."""
        mock_client = AsyncMock()
        mock_client.publish.side_effect = Exception("Connection failed")
        mock_redis_cls.from_url.return_value = mock_client

        manager = PubSubManager(TEST_REDIS_URL)

        mock_event = Mock()
        mock_event.model_dump_json.return_value = "{}"

        # Should not raise
        await manager.publish("channel", mock_event)

        # Should still close
        mock_client.close.assert_called_once()
