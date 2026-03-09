"""
Core storage package.

Provides generic repository interfaces and concrete implementations
for data storage (Interactions, Feedback).
"""

from typing import Optional

from core.config import StorageConfig, get_storage_config
from core.storage.interfaces import InteractionRepository, FeedbackRepository
from core.storage.models import Interaction, Feedback
from core.storage.postgres import PostgresStorage
from core.db.connection import close_pool
from core.db.schema import init_db

_storage_instance: Optional[PostgresStorage] = None


async def get_storage(config: Optional[StorageConfig] = None) -> PostgresStorage:
    """
    Get or create the global storage instance.
    """
    global _storage_instance

    if _storage_instance is None:
        if config is None:
            config = get_storage_config()

        _storage_instance = PostgresStorage(config)
        await _storage_instance.initialize()

    return _storage_instance


__all__ = [
    "InteractionRepository",
    "FeedbackRepository",
    "Interaction",
    "Feedback",
    "PostgresStorage",
    "get_storage",
    "close_pool",
    "init_db",
]
