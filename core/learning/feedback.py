"""
Learning & Adaptation Module.

Mechanisms for agents to improve performance over time based on feedback.
Supports pluggable persistence backends.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol
from uuid import UUID, uuid4

from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class FeedbackItem:
    """A unit of feedback for an agent's action."""

    agent_id: str
    task_id: str
    score: float  # Normalized 0.0 to 1.0
    comment: Optional[str] = None
    source: str = "human"  # human, system, self-correction
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for storage."""
        return {
            "id": str(self.id),
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "score": self.score,
            "comment": self.comment,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FeedbackItem":
        """Deserialize from dictionary."""
        return cls(
            id=UUID(data["id"]) if isinstance(data["id"], str) else data["id"],
            agent_id=data["agent_id"],
            task_id=data["task_id"],
            score=data["score"],
            comment=data.get("comment"),
            source=data.get("source", "human"),
            created_at=datetime.fromisoformat(data["created_at"])
            if isinstance(data["created_at"], str)
            else data["created_at"],
            metadata=data.get("metadata", {}),
        )


class FeedbackStore(Protocol):
    """Protocol for feedback persistence backends."""

    async def save(self, item: FeedbackItem) -> None:
        """Save a feedback item."""
        ...

    async def load_by_agent(self, agent_id: str) -> List[FeedbackItem]:
        """Load all feedback for an agent."""
        ...

    async def load_all(self) -> List[FeedbackItem]:
        """Load all feedback items."""
        ...


class InMemoryFeedbackStore:
    """In-memory feedback store (default, for testing)."""

    def __init__(self) -> None:
        self._items: List[FeedbackItem] = []

    async def save(self, item: FeedbackItem) -> None:
        """
        Store a feedback item in memory.

        Args:
            item: The FeedbackItem to save.
        """
        self._items.append(item)

    async def load_by_agent(self, agent_id: str) -> List[FeedbackItem]:
        """
        Retrieve all feedback for a specific agent.

        Args:
            agent_id: The unique identifier of the agent.

        Returns:
            A list of matching FeedbackItem objects.
        """
        return [f for f in self._items if f.agent_id == agent_id]

    async def load_all(self) -> List[FeedbackItem]:
        """
        Retrieve all stored feedback items.

        Returns:
            A full list of FeedbackItem objects.
        """
        return list(self._items)


class RedisFeedbackStore:
    """Redis-based feedback store for distributed persistence."""

    def __init__(self, redis_client: Any, key_prefix: str = "feedback:") -> None:
        self._redis = redis_client
        self._prefix = key_prefix

    async def save(self, item: FeedbackItem) -> None:
        """
        Persist a feedback item to Redis.

        Updates the item data, the agent-specific index, and the global list.

        Args:
            item: The FeedbackItem to save.
        """
        import json

        key = f"{self._prefix}{item.id}"
        # Store item data
        await self._redis.set(key, json.dumps(item.to_dict()))
        # Add to agent index
        agent_key = f"{self._prefix}agent:{item.agent_id}"
        await self._redis.sadd(agent_key, str(item.id))
        # Add to global list
        await self._redis.lpush(f"{self._prefix}all", str(item.id))

    async def load_by_agent(self, agent_id: str) -> List[FeedbackItem]:
        """
        Retrieve all feedback for an agent from Redis.

        Args:
            agent_id: The unique identifier of the agent.

        Returns:
            A list of FeedbackItem objects.
        """
        import json

        agent_key = f"{self._prefix}agent:{agent_id}"
        item_ids = await self._redis.smembers(agent_key)
        items = []
        for item_id in item_ids:
            data = await self._redis.get(f"{self._prefix}{item_id}")
            if data:
                items.append(FeedbackItem.from_dict(json.loads(data)))
        return items

    async def load_all(self) -> List[FeedbackItem]:
        """
        Retrieve all feedback items from Redis.

        Returns:
            A list of all stored FeedbackItem objects.
        """
        import json

        item_ids = await self._redis.lrange(f"{self._prefix}all", 0, -1)
        items = []
        for item_id in item_ids:
            data = await self._redis.get(f"{self._prefix}{item_id}")
            if data:
                items.append(FeedbackItem.from_dict(json.loads(data)))
        return items


class FeedbackCollector:
    """
    Collects and manages feedback for learning.

    Implements the Learning pattern:
    - Captures success/failure signals
    - Associates feedback with specific agents and tasks
    - Supports pluggable persistence backends
    - Serves as a data source for future tuning/RAG

    Example:
        # With Redis persistence
        from redis.asyncio import Redis
        redis = Redis.from_url("redis://localhost")
        store = RedisFeedbackStore(redis)
        collector = FeedbackCollector(store=store)

        # Log feedback
        await collector.log_feedback("agent-1", "task-123", 0.9, "Great response!")
    """

    def __init__(self, store: Optional[FeedbackStore] = None) -> None:
        """
        Initialize feedback collector.

        Args:
            store: Persistence backend (defaults to in-memory)
        """
        self._store: FeedbackStore = store or InMemoryFeedbackStore()
        self._cache: List[FeedbackItem] = []  # Local cache for quick access

    async def log_feedback(
        self,
        agent_id: str,
        task_id: str,
        score: float,
        comment: Optional[str] = None,
        source: str = "human",
        metadata: Optional[Dict] = None,
    ) -> FeedbackItem:
        """
        Log a new piece of feedback.

        Args:
            agent_id: ID of the agent being evaluated
            task_id: ID of the task/interaction
            score: Score from 0.0 to 1.0
            comment: Optional text comment
            source: Feedback source (human, system, self-correction)
            metadata: Additional metadata

        Returns:
            Created FeedbackItem
        """
        item = FeedbackItem(
            agent_id=agent_id,
            task_id=task_id,
            score=max(0.0, min(1.0, score)),
            comment=comment,
            source=source,
            metadata=metadata or {},
        )

        # Persist to store
        try:
            await self._store.save(item)
            self._cache.append(item)
            logger.debug(f"Feedback logged: agent={agent_id}, score={score:.2f}")
        except Exception as e:
            logger.error(f"Failed to persist feedback: {e}")
            # Still keep in cache for resilience
            self._cache.append(item)

        return item

    async def get_agent_performance(self, agent_id: str) -> Dict[str, Any]:
        """
        Calculate performance metrics for an agent.

        Args:
            agent_id: Agent to analyze

        Returns:
            Performance metrics dict
        """
        try:
            feedback = await self._store.load_by_agent(agent_id)
        except Exception:
            # Fallback to cache
            feedback = [f for f in self._cache if f.agent_id == agent_id]

        if not feedback:
            return {"average_score": 0.0, "count": 0, "trend": "unknown"}

        scores = [f.score for f in feedback]
        avg_score = sum(scores) / len(scores)

        # Calculate trend (last 5 vs previous 5)
        trend = "stable"
        if len(scores) >= 10:
            recent = sum(scores[-5:]) / 5
            previous = sum(scores[-10:-5]) / 5
            if recent > previous + 0.1:
                trend = "improving"
            elif recent < previous - 0.1:
                trend = "declining"

        return {
            "average_score": avg_score,
            "count": len(feedback),
            "trend": trend,
            "min_score": min(scores),
            "max_score": max(scores),
        }

    async def get_all_feedback(self) -> List[FeedbackItem]:
        """Get all stored feedback."""
        try:
            return await self._store.load_all()
        except Exception:
            return list(self._cache)


__all__ = [
    "FeedbackItem",
    "FeedbackStore",
    "FeedbackCollector",
    "InMemoryFeedbackStore",
    "RedisFeedbackStore",
]
