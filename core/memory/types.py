"""
Memory Data Types.

This module provides the primary data structures used throughout the
memory and orchestration layers. It includes the MemoryType enum for
hierarchical storage and the MemoryItem dataclass for structured content.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4


class MemoryType(Enum):
    """
    Classifies the longevity and purpose of stored information.
    """

    SHORT_TERM = "short_term"  # Working memory, context window
    LONG_TERM = "long_term"  # Knowledge base, vector store
    EPISODIC = "episodic"  # Past experiences, event logs
    ENTITY = "entity"  # Profiles, user preferences, facts


@dataclass
class MemoryItem:
    """
    Represents a single atomic unit of information in the memory system.

    Attributes:
        content: The text payload of the memory.
        memory_type: The classification (Short-term, Long-term, etc.).
        id: Unique UUID for the item.
        created_at: UTC timestamp of creation.
        metadata: Extensible dictionary for tags, source URLs, etc.
        score: Relevance or importance weight (standardized to 0.0-1.0).
        embedding: Optional vector representation for semantic search.
    """

    content: str
    memory_type: MemoryType
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 1.0  # Relevance/Importance score
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": str(self.id),
            "content": self.content,
            "type": self.memory_type.value,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryItem":
        """Create from dictionary."""
        # Handle 'type' vs 'memory_type' key diff
        mem_type_val = data.get(
            "type", data.get("memory_type", MemoryType.SHORT_TERM.value)
        )
        try:
            mem_type = MemoryType(mem_type_val)
        except ValueError:
            mem_type = MemoryType.SHORT_TERM

        # Handle datetime parsing
        created_at_val = data.get("created_at")
        if isinstance(created_at_val, str):
            try:
                created_at = datetime.fromisoformat(created_at_val)
            except ValueError:
                created_at = datetime.now(timezone.utc)
        else:
            created_at = datetime.now(timezone.utc)

        return cls(
            id=UUID(str(data.get("id"))) if data.get("id") else uuid4(),
            content=data.get("content", ""),
            memory_type=mem_type,
            created_at=created_at,
            metadata=data.get("metadata", {}),
            score=float(data.get("score", 1.0)),
            embedding=data.get("embedding"),
        )
