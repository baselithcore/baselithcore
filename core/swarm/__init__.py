"""
Swarm Intelligence Module

Provides emergent baselith-core coordination through swarm behaviors.
Implements auction-based task allocation, pheromone signaling, and
dynamic team formation without centralized control.

Key Concepts:
- Colony: A group of agents with shared goals
- Auction: Decentralized task allocation mechanism
- Pheromones: Virtual signals for indirect communication
- Team Formation: Dynamic grouping based on capabilities
"""

from core.config.swarm import AuctionConfig, SwarmConfig, TeamConfig

from .auction import TaskAuction
from .colony import Colony
from .pheromones import PheromoneSystem
from .team_formation import TeamFormationEngine
from .types import (
    AgentProfile,
    AgentStatus,
    Bid,
    Capability,
    MessageType,
    SwarmMessage,
    Task,
    TaskPriority,
    TeamFormation,
)

BatchResult = Colony.BatchResult

__all__ = [
    # Types
    "AgentProfile",
    "Task",
    "Bid",
    "SwarmMessage",
    "TeamFormation",
    "AgentStatus",
    "MessageType",
    "TaskPriority",
    "Capability",
    # Config
    "SwarmConfig",
    "AuctionConfig",
    "TeamConfig",
    # Colony
    "Colony",
    "BatchResult",
    # Auction
    "TaskAuction",
    # Pheromones
    "PheromoneSystem",
    # Team
    "TeamFormationEngine",
]
