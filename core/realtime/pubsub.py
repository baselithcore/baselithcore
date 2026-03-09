"""
Real-time Pub/Sub Manager.

Provides Redis-backed broadcasting and subscription capabilities
for real-time system events.
"""

import asyncio
from core.observability.logging import get_logger
import json
from typing import AsyncGenerator
from redis.asyncio import Redis as AsyncRedis
from core.realtime.events import RealtimeEvent

logger = get_logger(__name__)

CHANNEL_PREFIX = "events:"


class PubSubManager:
    """
    Redis-backed Real-time Message Broker.

    Facilitates low-latency event broadcasting for Server-Sent Events (SSE).
    Coordinates between internal system events and external client
    subscriptions, ensuring efficient delivery of streaming data and
    asynchronous notifications across distributed instances.
    """

    def __init__(self, redis_url: str):
        """
        Initialize the PubSub manager.

        Args:
            redis_url: Connection string for the Redis instance.
        """
        self.redis_url = redis_url

    async def get_redis_async(self) -> AsyncRedis:
        """Get async redis client."""
        return AsyncRedis.from_url(self.redis_url, decode_responses=True)

    async def publish(self, channel: str, event: RealtimeEvent):
        """Publish an event to a specific channel."""
        full_channel = f"{CHANNEL_PREFIX}{channel}"
        client = None
        try:
            client = await self.get_redis_async()
            await client.publish(full_channel, event.model_dump_json())
        except Exception as e:
            logger.error(f"[pubsub] Failed to publish to {full_channel}: {e}")
        finally:
            if client:
                await client.close()

    async def subscribe(self, channels: list[str]) -> AsyncGenerator[dict, None]:
        """
        Yields SSE-compatible messages from Redis channels.
        Always includes 'global' channel.
        """
        full_channels = [f"{CHANNEL_PREFIX}global"] + [
            f"{CHANNEL_PREFIX}{c}" for c in channels if c != "global"
        ]
        client = await self.get_redis_async()

        # Add a ping to keep connection alive if needed, but Redis pubsub typically blocks

        try:
            async with client.pubsub() as pubsub:
                await pubsub.subscribe(*full_channels)
                logger.info(f"[pubsub] Subscribed to {full_channels}")

                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            # Parse JSON to validate/structure if needed,
                            # but for SSE we can pass data through or re-wrap.
                            # We expect the payload to be the JSON string of RealtimeEvent
                            data_str = message["data"]
                            # Optional: validate it's a RealtimeEvent
                            # event = RealtimeEvent.model_validate_json(data_str)

                            # SSE format: event name is often helpful.
                            # We decode to look at 'type' field to set SSE 'event' field
                            event_data = json.loads(data_str)
                            event_type = event_data.get("type", "message")

                            yield {
                                "event": event_type,
                                "data": data_str,  # Send full JSON as data
                            }
                        except Exception as e:
                            logger.warning(f"[pubsub] Malformed message: {e}")

        except asyncio.CancelledError:
            logger.info("[pubsub] Subscription cancelled.")
        finally:
            await client.close()
