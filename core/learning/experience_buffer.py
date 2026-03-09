"""
Prioritized Experience Replay (PER) Buffer.

Implements a sophisticated memory management system for agent experiences.
Uses a `SumTree` for efficient O(log n) sampling based on priority
scores, typically derived from TD-errors, allowing the agent to learn
more effectively from surprising or informative outcomes.
"""

import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from collections import deque
import numpy as np

from core.observability.logging import get_logger
from .types import Experience, Episode

logger = get_logger(__name__)


@dataclass
class SampledBatch:
    """A batch of sampled experiences with metadata for PER."""

    experiences: List[Experience]
    indices: List[int]
    weights: np.ndarray  # Importance sampling weights

    def __len__(self) -> int:
        return len(self.experiences)


class SumTree:
    """
    Binary sum tree for efficient priority-based sampling.

    Allows O(log n) sampling proportional to priorities and O(log n) updates.
    Used by Prioritized Experience Replay.
    """

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.tree = np.zeros(2 * capacity - 1)
        self.data = [None] * capacity
        self.write_idx = 0
        self.size = 0

    def add(self, priority: float, data) -> int:
        """Add data with priority, return index."""
        idx = self.write_idx + self.capacity - 1
        data_idx = self.write_idx

        self.data[self.write_idx] = data
        self.update(idx, priority)

        self.write_idx = (self.write_idx + 1) % self.capacity
        self.size = min(self.size + 1, self.capacity)

        return data_idx

    def update(self, tree_idx: int, priority: float) -> None:
        """Update priority at tree index."""
        change = priority - self.tree[tree_idx]
        self.tree[tree_idx] = priority

        # Propagate change up the tree
        while tree_idx > 0:
            tree_idx = (tree_idx - 1) // 2
            self.tree[tree_idx] += change

    def get(self, s: float) -> Tuple[int, float, Experience]:
        """
        Get experience by cumulative priority value s.

        Returns:
            (tree_idx, priority, experience)
        """
        idx = 0

        while idx < self.capacity - 1:  # Not a leaf
            left = 2 * idx + 1
            right = left + 1

            if s <= self.tree[left]:
                idx = left
            else:
                s -= self.tree[left]
                idx = right

        data_idx = idx - self.capacity + 1
        return idx, self.tree[idx], self.data[data_idx]  # type: ignore[return-value]

    @property
    def total(self) -> float:
        """Total priority sum."""
        return self.tree[0]

    @property
    def min_priority(self) -> float:
        """Minimum non-zero priority for importance sampling."""
        non_zero = self.tree[self.capacity - 1 : self.capacity - 1 + self.size]
        return min(p for p in non_zero if p > 0) if len(non_zero) > 0 else 1.0


class ExperienceReplay:
    """
    High-performance storage for agentic experiences.

    Supports both uniform and prioritized sampling strategies. Integrates
    importance sampling weights to correct for the bias introduced by
    prioritization, ensuring stable and unbiased policy updates.
    """

    def __init__(
        self,
        capacity: int = 10000,
        prioritized: bool = False,
        priority_alpha: float = 0.6,
        priority_beta: float = 0.4,
        priority_beta_increment: float = 0.001,
        priority_epsilon: float = 1e-6,
    ):
        """
        Initialize experience replay buffer.

        Args:
            capacity: Maximum number of experiences
            prioritized: Use prioritized experience replay
            priority_alpha: Priority exponent (0=uniform, 1=full priority)
            priority_beta: Importance sampling exponent (0=no correction, 1=full)
            priority_beta_increment: How much to increase beta each sample
            priority_epsilon: Small constant added to priorities
        """
        self.capacity = capacity
        self.prioritized = prioritized
        self.priority_alpha = priority_alpha
        self.priority_beta = priority_beta
        self.priority_beta_increment = priority_beta_increment
        self.priority_epsilon = priority_epsilon

        if prioritized:
            self._tree = SumTree(capacity)
        else:
            self._buffer: deque = deque(maxlen=capacity)

        self._priorities: List[float] = []
        self._episodes: List[Episode] = []
        self._current_episode: Optional[Episode] = None
        self._max_priority = 1.0

    def add(self, experience: Experience, priority: Optional[float] = None) -> None:
        """
        Add experience to buffer.

        Args:
            experience: Experience to add
            priority: Optional priority value (defaults to max priority)
        """
        if self.prioritized:
            # Priority is stored as p^alpha
            priority_val = self._max_priority if priority is None else priority
            priority_val = (priority_val + self.priority_epsilon) ** self.priority_alpha
            self._tree.add(priority_val, experience)
        else:
            self._buffer.append(experience)

        # Add to current episode if active
        if self._current_episode:
            self._current_episode.add_experience(experience)

    def sample(
        self,
        batch_size: int,
        beta: Optional[float] = None,
    ) -> SampledBatch:
        """
        Sample batch of experiences with importance sampling weights.

        Args:
            batch_size: Number of experiences to sample
            beta: Importance sampling exponent (uses self.priority_beta if None)

        Returns:
            SampledBatch with experiences, indices, and weights
        """
        if self.prioritized:
            return self._prioritized_sample(batch_size, beta)
        else:
            # Uniform sampling
            if len(self._buffer) < batch_size:
                experiences = list(self._buffer)
            else:
                experiences = random.sample(list(self._buffer), batch_size)  # nosec B311

            return SampledBatch(
                experiences=experiences,
                indices=list(range(len(experiences))),
                weights=np.ones(len(experiences)),
            )

    def _prioritized_sample(
        self,
        batch_size: int,
        beta: Optional[float] = None,
    ) -> SampledBatch:
        """Sample with priority weighting and importance sampling."""
        beta = beta if beta is not None else self.priority_beta

        # Anneal beta towards 1
        self.priority_beta = min(1.0, self.priority_beta + self.priority_beta_increment)

        experiences = []
        indices = []
        priorities = []

        # Segment the priority range
        segment = self._tree.total / batch_size

        for i in range(batch_size):
            # Sample from segment
            low = segment * i
            high = segment * (i + 1)
            s = random.uniform(low, high)  # nosec B311

            tree_idx, priority, experience = self._tree.get(s)

            if experience is not None:
                experiences.append(experience)
                indices.append(tree_idx)
                priorities.append(priority)

        # Calculate importance sampling weights
        if experiences:
            # P(i) = p_i / sum(p)
            probs = np.array(priorities) / self._tree.total

            # w_i = (N * P(i))^(-beta)
            n = self._tree.size
            weights = (n * probs) ** (-beta)

            # Normalize by max weight
            weights = weights / weights.max()
        else:
            weights = np.array([])

        return SampledBatch(
            experiences=experiences,
            indices=indices,
            weights=weights,
        )

    def update_priorities(
        self,
        indices: List[int],
        td_errors: List[float],
    ) -> None:
        """
        Update priorities based on TD-errors.

        Args:
            indices: Tree indices from sampling
            td_errors: TD-errors for each experience
        """
        if not self.prioritized:
            return

        for idx, td_error in zip(indices, td_errors):
            # Priority = |TD-error| + epsilon
            priority = (abs(td_error) + self.priority_epsilon) ** self.priority_alpha
            self._tree.update(idx, priority)
            self._max_priority = max(self._max_priority, priority)

    def update_priority(self, index: int, priority: float) -> None:
        """Update priority of an experience (legacy interface)."""
        if self.prioritized:
            priority = (priority + self.priority_epsilon) ** self.priority_alpha
            self._tree.update(index, priority)
            self._max_priority = max(self._max_priority, priority)

    def start_episode(self, context: Optional[Dict] = None) -> Episode:
        """
        Start a new episode.

        Args:
            context: Episode context

        Returns:
            New episode
        """
        self._current_episode = Episode(context=context or {})
        return self._current_episode

    def end_episode(self, success: bool = False) -> Optional[Episode]:
        """
        End current episode.

        Args:
            success: Whether episode was successful

        Returns:
            Completed episode
        """
        if not self._current_episode:
            return None

        self._current_episode.end(success)
        self._episodes.append(self._current_episode)

        episode = self._current_episode
        self._current_episode = None

        return episode

    def sample_episodes(self, count: int) -> List[Episode]:
        """
        Sample complete episodes.

        Args:
            count: Number of episodes to sample

        Returns:
            List of episodes
        """
        if len(self._episodes) < count:
            return self._episodes.copy()
        return random.sample(self._episodes, count)  # nosec B311

    def get_positive_experiences(self, count: int) -> List[Experience]:
        """Get positive experiences for learning."""
        buffer = self._get_all_experiences()
        positive = [e for e in buffer if e.is_positive]
        if len(positive) < count:
            return positive
        return random.sample(positive, count)  # nosec B311

    def get_negative_experiences(self, count: int) -> List[Experience]:
        """Get negative experiences for learning."""
        buffer = self._get_all_experiences()
        negative = [e for e in buffer if not e.is_positive]
        if len(negative) < count:
            return negative
        return random.sample(negative, count)  # nosec B311

    def get_recent(self, count: int) -> List[Experience]:
        """Get most recent experiences."""
        buffer = self._get_all_experiences()
        return buffer[-count:]

    def _get_all_experiences(self) -> List[Experience]:
        """Get all experiences from buffer."""
        if self.prioritized:
            exps = [e for e in self._tree.data[: self._tree.size] if e is not None]
            return exps  # type: ignore[return-value]
        return list(self._buffer)

    def clear(self) -> None:
        """Clear the buffer."""
        if self.prioritized:
            self._tree = SumTree(self.capacity)
            self._max_priority = 1.0
        else:
            self._buffer.clear()
        self._priorities.clear()
        self._episodes.clear()
        self._current_episode = None

    @property
    def size(self) -> int:
        """Current buffer size."""
        if self.prioritized:
            return self._tree.size
        return len(self._buffer)

    @property
    def is_full(self) -> bool:
        """Check if buffer is at capacity."""
        return self.size >= self.capacity

    def get_stats(self) -> Dict:
        """Get buffer statistics."""
        buffer = self._get_all_experiences()
        positive = sum(1 for e in buffer if e.is_positive)

        stats = {
            "size": self.size,
            "capacity": self.capacity,
            "utilization": self.size / self.capacity if self.capacity > 0 else 0,
            "positive_ratio": positive / self.size if self.size > 0 else 0,
            "episodes": len(self._episodes),
            "prioritized": self.prioritized,
        }

        if self.prioritized:
            stats["priority_beta"] = self.priority_beta
            stats["max_priority"] = self._max_priority
            stats["total_priority"] = self._tree.total

        return stats
