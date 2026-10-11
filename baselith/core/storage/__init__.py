"""
Core storage package.

Provides generic repository interfaces and concrete implementations
for data storage (Interactions, Feedback).
"""

from typing import Optional

from baselith.core.config import StorageConfig, get_storage_config
from baselith.core.db.connection import close_pool
from baselith.core.db.schema import init_db
from baselith.core.storage.interfaces import FeedbackRepository, InteractionRepository
from baselith.core.storage.models import Feedback, Interaction
from baselith.core.storage.postgres import PostgresStorage

_storage_instance: PostgresStorage | None = None


async def get_storage(config: StorageConfig | None = None) -> PostgresStorage:
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
    "Feedback",
    "FeedbackRepository",
    "Interaction",
    "InteractionRepository",
    "PostgresStorage",
    "close_pool",
    "get_storage",
    "init_db",
]
