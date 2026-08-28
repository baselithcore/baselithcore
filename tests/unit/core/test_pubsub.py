"""
Tests for core.realtime.pubsub module.
"""

from unittest.mock import AsyncMock, Mock, patch

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
    @patch("core.realtime.pubsub.create_redis_client")
    async def test_get_redis_async(self, mock_factory):
        """Clients come off the shared bounded pool, not a per-call pool."""
        mock_client = Mock()
        mock_factory.return_value = mock_client

        manager = PubSubManager(TEST_REDIS_URL)
        result = await manager.get_redis_async()

        mock_factory.assert_called_once_with(TEST_REDIS_URL, decode_responses=True)
        assert result == mock_client

    @pytest.mark.asyncio
    @patch("core.realtime.pubsub.create_redis_client")
    async def test_publish_sends_event(self, mock_factory):
        """Test publish sends event to redis."""
        mock_client = AsyncMock()
        mock_factory.return_value = mock_client

        manager = PubSubManager(TEST_REDIS_URL)

        mock_event = Mock()
        mock_event.model_dump_json.return_value = '{"type": "test"}'

        await manager.publish("test_channel", mock_event)

        mock_client.publish.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.realtime.pubsub.create_redis_client")
    async def test_publisher_is_reused_across_events(self, mock_factory):
        """One client per process, not one TCP connect + teardown per event."""
        mock_factory.return_value = AsyncMock()
        manager = PubSubManager(TEST_REDIS_URL)

        event = Mock()
        event.model_dump_json.return_value = "{}"
        for _ in range(5):
            await manager.publish("c", event)

        assert mock_factory.call_count == 1
        assert mock_factory.return_value.publish.await_count == 5

    @pytest.mark.asyncio
    @patch("core.realtime.pubsub.create_redis_client")
    async def test_publish_handles_error_gracefully(self, mock_factory):
        """Test publish handles errors gracefully."""
        mock_client = AsyncMock()
        mock_client.publish.side_effect = Exception("Connection failed")
        mock_factory.return_value = mock_client

        manager = PubSubManager(TEST_REDIS_URL)

        mock_event = Mock()
        mock_event.model_dump_json.return_value = "{}"

        # Should not raise
        await manager.publish("channel", mock_event)

    @pytest.mark.asyncio
    @patch("core.realtime.pubsub.create_redis_client")
    async def test_close_releases_the_publisher(self, mock_factory):
        mock_client = AsyncMock()
        mock_factory.return_value = mock_client
        manager = PubSubManager(TEST_REDIS_URL)

        event = Mock()
        event.model_dump_json.return_value = "{}"
        await manager.publish("c", event)
        await manager.close()

        mock_client.aclose.assert_awaited_once()
        # Idempotent.
        await manager.close()
        assert mock_client.aclose.await_count == 1
