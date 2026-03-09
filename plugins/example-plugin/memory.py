"""Example Plugin Memory Integration.

Demonstrates how to interact with the core AgentMemory system.
"""

from core.observability.logging import get_logger
import json
from typing import Any, Dict, List
from core.memory.types import MemoryType

logger = get_logger(__name__)


class ExampleMemory:
    """Memory manager for the example plugin."""

    def __init__(self, agent_memory=None):
        """Initialize memory."""
        self._memory = agent_memory
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize memory system."""
        if self._initialized:
            return

        try:
            if self._memory is None:
                from core.memory import get_memory

                self._memory = get_memory()

            self._initialized = True
            logger.info("Example memory initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize example memory: {e}")

    async def store_item_memory(self, item_id: str, data: Dict[str, Any]) -> None:
        """Store item in long-term memory.

        Args:
            item_id: Unique identifier for the item
            data: Data to store
        """
        if not self._memory:
            return

        try:
            content = json.dumps(data)
            await self._memory.add_memory(
                content=content,
                memory_type=MemoryType.LONG_TERM,
                metadata={
                    "type": "example_item",
                    "item_id": item_id,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to store item memory: {e}")

    async def recall_items(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Recall items from memory based on similarity.

        Args:
            query: Search query
            limit: Max results

        Returns:
            List of recalled item data
        """
        if not self._memory:
            return []

        try:
            results = await self._memory.recall(
                query=query,
                memory_type=MemoryType.LONG_TERM,
                limit=limit,
                filters={"type": "example_item"},
            )

            items = []
            for res in results:
                if isinstance(res.content, str):
                    try:
                        items.append(json.loads(res.content))
                    except json.JSONDecodeError:
                        continue
                elif isinstance(res.content, dict):
                    items.append(res.content)

            return items

        except Exception as e:
            logger.warning(f"Error recalling items: {e}")
            return []
