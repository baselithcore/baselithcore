"""
Document Indexing State management logic.

Handles persistent indexing state using Redis, fingerprinting-based change detection,
and indexing statistics tracking.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class IndexingStats:
    """Statistics from an indexing run."""

    new_documents: int = 0
    skipped_documents: int = 0
    deleted_documents: int = 0
    graph_writes: int = 0
    duration_seconds: float = 0.0
    per_origin: Dict[str, int] = field(default_factory=dict)


@dataclass
class IndexedDocument:
    """Tracked state of an indexed document."""

    fingerprint: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    mtime: Optional[float] = None
    size: Optional[int] = None


class IndexStateStore:
    """
    Manages persistence and retrieval of indexing state.
    """

    def __init__(self, redis_state_key: str = "baselith:indexing:state"):
        self._redis_state_key = redis_state_key
        self._redis: Optional[Any] = None
        self._state_loaded = False

    def _get_redis_client(self):
        """Initialize and retrieve the Redis client."""
        if self._redis:
            return self._redis

        try:
            from core.cache import create_redis_client
            from core.config import get_storage_config

            config = get_storage_config()
            if not config.cache_redis_url:
                logger.warning("[indexing] No Redis URL configured for state persistence")
                return None

            self._redis = create_redis_client(config.cache_redis_url)
            return self._redis
        except Exception as e:
            logger.error(f"[indexing] Failed to initialize Redis client: {e}")
            return None

    async def load_state(self) -> Dict[str, IndexedDocument]:
        """Fetch the previous indexing state from the persistence layer."""
        indexed_items: Dict[str, IndexedDocument] = {}
        redis = self._get_redis_client()
        if redis is None:
            return indexed_items

        try:
            data = await redis.get(self._redis_state_key)
            if data:
                state = json.loads(data)
                for uid, doc_data in state.items():
                    indexed_items[uid] = IndexedDocument(
                        fingerprint=doc_data["fingerprint"],
                        metadata=doc_data.get("metadata", {}),
                    )
                logger.info("[indexing] Loaded %d document states from Redis", len(indexed_items))
        except Exception as e:
            logger.warning(f"[indexing] Failed to load state from Redis: {e}")
        
        return indexed_items

    async def save_state(self, indexed_items: Dict[str, IndexedDocument]) -> None:
        """Persist the current indexing state to the persistence layer."""
        redis = self._get_redis_client()
        if redis is None:
            return

        try:
            state = {
                uid: {
                    "fingerprint": doc.fingerprint,
                    "metadata": doc.metadata,
                }
                for uid, doc in indexed_items.items()
            }
            await redis.set(self._redis_state_key, json.dumps(state))
            logger.debug("[indexing] Saved %d document states to Redis", len(indexed_items))
        except Exception as e:
            logger.warning(f"[indexing] Failed to save state to Redis: {e}")

    async def close(self) -> None:
        """Close Redis resources."""
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception as e:
                logger.warning(f"Error closing Redis client: {e}")
            finally:
                self._redis = None
