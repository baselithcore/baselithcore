"""
Learning Types

Core data structures for continuous learning.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid


class RewardType(Enum):
    """Types of rewards."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class LearningPhase(Enum):
    """Phases of learning process."""

    COLLECTING = "collecting"
    TRAINING = "training"
    EVALUATING = "evaluating"
    DEPLOYING = "deploying"


@dataclass
class Experience:
    """
    A single experience from agent interaction.

    Captures state, action, reward, and outcome for learning.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: Dict[str, Any] = field(default_factory=dict)
    action: str = ""
    action_params: Dict[str, Any] = field(default_factory=dict)
    reward: float = 0.0
    reward_type: RewardType = RewardType.NEUTRAL
    next_state: Optional[Dict[str, Any]] = None
    outcome: str = ""
    success: bool = False
    metadata: Dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_positive(self) -> bool:
        """Check if experience was positive."""
        return self.reward > 0 or self.success

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "state": self.state,
            "action": self.action,
            "action_params": self.action_params,
            "reward": self.reward,
            "reward_type": self.reward_type.value,
            "next_state": self.next_state,
            "outcome": self.outcome,
            "success": self.success,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class Reward:
    """
    A reward signal for an action.
    """

    value: float
    reason: str = ""
    source: str = "system"  # system, user, self
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def reward_type(self) -> RewardType:
        """Determine reward type from value."""
        if self.value > 0.1:
            return RewardType.POSITIVE
        elif self.value < -0.1:
            return RewardType.NEGATIVE
        return RewardType.NEUTRAL


@dataclass
class Episode:
    """
    A sequence of experiences forming an episode.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    experiences: List[Experience] = field(default_factory=list)
    total_reward: float = 0.0
    success: bool = False
    context: Dict = field(default_factory=dict)
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None

    def add_experience(self, exp: Experience) -> None:
        """Add experience to episode."""
        self.experiences.append(exp)
        self.total_reward += exp.reward

    def end(self, success: bool = False) -> None:
        """End the episode."""
        self.success = success
        self.ended_at = datetime.now()

    @property
    def length(self) -> int:
        """Number of experiences in episode."""
        return len(self.experiences)

    @property
    def avg_reward(self) -> float:
        """Average reward per experience."""
        if not self.experiences:
            return 0.0
        return self.total_reward / len(self.experiences)


@dataclass
class LearningMetrics:
    """
    Metrics for learning performance.
    """

    total_experiences: int = 0
    total_episodes: int = 0
    positive_experiences: int = 0
    negative_experiences: int = 0
    avg_reward: float = 0.0
    success_rate: float = 0.0
    learning_rate: float = 0.01
    exploration_rate: float = 0.1
    last_update: Optional[datetime] = None

    def update(self, experience: Experience) -> None:
        """Update metrics with new experience."""
        self.total_experiences += 1
        if experience.is_positive:
            self.positive_experiences += 1
        else:
            self.negative_experiences += 1

        # Update average reward (rolling)
        alpha = 0.1
        self.avg_reward = (1 - alpha) * self.avg_reward + alpha * experience.reward
        self.last_update = datetime.now()

    def get_summary(self) -> Dict:
        """Get metrics summary."""
        return {
            "total_experiences": self.total_experiences,
            "total_episodes": self.total_episodes,
            "positive_ratio": (
                self.positive_experiences / self.total_experiences
                if self.total_experiences > 0
                else 0
            ),
            "avg_reward": self.avg_reward,
            "success_rate": self.success_rate,
        }
