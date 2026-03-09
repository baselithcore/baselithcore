"""
Decentralized Task Allocation via Auction.

Implements market-based mechanisms for distributing work across a
swarm. Agents compete for tasks based on their specialized
capabilities and current operational load.
"""

from core.observability.logging import get_logger
from typing import Dict, List, Optional, Callable

from core.config.swarm import AuctionConfig
from .types import Task, Bid, AgentProfile, TaskPriority

logger = get_logger(__name__)


class TaskAuction:
    """
    Marketplace for swarm task distribution.

    Manages the lifecycle of a task auction, from announcement to bid
    collection and resolution. Uses a multi-factor scoring algorithm
    that balances agent proficiency with success rates and system health.
    """

    def __init__(
        self,
        config: Optional[AuctionConfig] = None,
        scoring_fn: Optional[Callable[[AgentProfile, Task], float]] = None,
    ):
        """
        Initialize auction system.

        Args:
            config: Auction configuration
            scoring_fn: Custom scoring function for bids
        """
        self.config = config or AuctionConfig()
        self.scoring_fn = scoring_fn or self._default_scoring

        # Task ID -> List of bids
        self._pending_auctions: Dict[str, List[Bid]] = {}
        # Task ID -> Task
        self._tasks: Dict[str, Task] = {}
        # Task ID -> Winner
        self._resolved: Dict[str, str] = {}

    def _default_scoring(self, agent: AgentProfile, task: Task) -> float:
        """Default bid scoring based on capability match and load."""
        cap_score = agent.get_capability_score(task.required_capabilities)
        load_penalty = 1.0 - agent.current_load
        success_bonus = agent.success_rate

        # Priority bonus
        priority_mult = {
            TaskPriority.LOW: 1.0,
            TaskPriority.NORMAL: 1.0,
            TaskPriority.HIGH: 1.1,
            TaskPriority.CRITICAL: 1.2,
        }.get(task.priority, 1.0)

        return (
            cap_score * 0.5 + load_penalty * 0.3 + success_bonus * 0.2
        ) * priority_mult

    def announce_task(self, task: Task) -> None:
        """
        Announce a task for bidding.

        Args:
            task: Task to be auctioned
        """
        logger.info(f"Announcing task {task.id} for auction")
        self._tasks[task.id] = task
        self._pending_auctions[task.id] = []

    def submit_bid(self, bid: Bid) -> bool:
        """
        Submit a bid for a task.

        Args:
            bid: The bid to submit

        Returns:
            True if bid was accepted, False otherwise
        """
        if bid.task_id not in self._pending_auctions:
            logger.warning(f"No auction found for task {bid.task_id}")
            return False

        if bid.task_id in self._resolved:
            logger.warning(f"Auction for task {bid.task_id} already resolved")
            return False

        bids = self._pending_auctions[bid.task_id]

        # Check max bids limit
        if len(bids) >= self.config.max_bids:
            logger.warning(f"Max bids reached for task {bid.task_id}")
            return False

        # Check for duplicate bids from same agent
        if any(b.agent_id == bid.agent_id for b in bids):
            logger.warning(f"Agent {bid.agent_id} already bid on task {bid.task_id}")
            return False

        bids.append(bid)
        logger.info(
            f"Bid accepted: agent={bid.agent_id}, task={bid.task_id}, score={bid.score:.2f}"
        )
        return True

    def calculate_bid(self, agent: AgentProfile, task: Task) -> Bid:
        """
        Calculate optimal bid for an agent on a task.

        Args:
            agent: Agent profile
            task: Task to bid on

        Returns:
            Calculated bid
        """
        score = self.scoring_fn(agent, task)

        # Estimate completion time based on capabilities
        cap_score = agent.get_capability_score(task.required_capabilities)
        base_time = 10.0  # seconds
        estimated_time = base_time / max(cap_score, 0.1)

        return Bid(
            agent_id=agent.id,
            task_id=task.id,
            score=score,
            estimated_time=estimated_time,
            confidence=cap_score,
        )

    def resolve(self, task_id: str) -> Optional[str]:
        """
        Resolve auction and determine winner.

        Args:
            task_id: ID of the task to resolve

        Returns:
            Winner agent ID, or None if no valid bids
        """
        if task_id not in self._pending_auctions:
            logger.error(f"No auction found for task {task_id}")
            return None

        bids = self._pending_auctions[task_id]

        if len(bids) < self.config.min_bids:
            logger.warning(
                f"Not enough bids for task {task_id}: {len(bids)}/{self.config.min_bids}"
            )
            return None

        # Sort by combined score (descending)
        sorted_bids = sorted(bids, key=lambda b: b.combined_score, reverse=True)
        winner = sorted_bids[0]

        # Handle ties
        top_score = winner.combined_score
        tied = [b for b in sorted_bids if abs(b.combined_score - top_score) < 0.001]

        if len(tied) > 1:
            winner = self._break_tie(tied)

        # Record winner
        self._resolved[task_id] = winner.agent_id

        # Update task
        if task_id in self._tasks:
            self._tasks[task_id].assigned_to = winner.agent_id
            self._tasks[task_id].status = "assigned"

        logger.info(
            f"Auction resolved: task={task_id}, winner={winner.agent_id}, score={winner.combined_score:.2f}"
        )

        # Cleanup
        del self._pending_auctions[task_id]

        return winner.agent_id

    def _break_tie(self, bids: List[Bid]) -> Bid:
        """Break tie between equal bids."""
        if self.config.tie_breaker == "first":
            return min(bids, key=lambda b: b.timestamp)
        elif self.config.tie_breaker == "load":
            # Would need agent info - fallback to first
            return min(bids, key=lambda b: b.timestamp)
        else:  # random
            import random

            return random.choice(bids)  # nosec B311

    def get_pending_auctions(self) -> List[str]:
        """Get list of pending auction task IDs."""
        return list(self._pending_auctions.keys())

    def get_bids(self, task_id: str) -> List[Bid]:
        """Get all bids for a task."""
        return self._pending_auctions.get(task_id, [])

    def cancel_auction(self, task_id: str) -> bool:
        """Cancel an ongoing auction."""
        if task_id in self._pending_auctions:
            del self._pending_auctions[task_id]
            if task_id in self._tasks:
                self._tasks[task_id].status = "cancelled"
            logger.info(f"Auction cancelled for task {task_id}")
            return True
        return False
