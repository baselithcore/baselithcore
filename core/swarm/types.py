"""
Fundamental Types for Swarm Intelligence.

Defines the core domain model for decentralized multi-agent systems,
including agent profiles, task definitions, and communication schema.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set
from datetime import datetime
import uuid


class AgentStatus(Enum):
    """
    Operational states of a swarm-registered agent.
    """

    IDLE = "idle"
    BUSY = "busy"
    BIDDING = "bidding"
    OFFLINE = "offline"


class TaskPriority(Enum):
    """Task priority levels."""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class MessageType(Enum):
    """Types of swarm messages."""

    TASK_ANNOUNCEMENT = "task_announcement"
    BID = "bid"
    BID_ACCEPTED = "bid_accepted"
    BID_REJECTED = "bid_rejected"
    PHEROMONE = "pheromone"
    HEARTBEAT = "heartbeat"
    TEAM_INVITE = "team_invite"
    TEAM_RESPONSE = "team_response"


@dataclass
class Capability:
    """An agent capability."""

    name: str
    proficiency: float = 1.0  # 0.0 to 1.0
    metadata: Dict = field(default_factory=dict)


@dataclass
class AgentProfile:
    """Profile of an agent in the swarm."""

    id: str
    name: str
    capabilities: List[Capability] = field(default_factory=list)
    status: AgentStatus = AgentStatus.IDLE
    current_load: float = 0.0  # 0.0 to 1.0
    success_rate: float = 1.0
    metadata: Dict = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        """Check if agent is available for tasks."""
        return self.status == AgentStatus.IDLE and self.current_load < 0.9

    def has_capability(self, name: str, min_proficiency: float = 0.0) -> bool:
        """Check if agent has a specific capability."""
        for cap in self.capabilities:
            if cap.name == name and cap.proficiency >= min_proficiency:
                return True
        return False

    def get_capability_score(self, required: List[str]) -> float:
        """Calculate capability match score for requirements."""
        if not required:
            return 1.0

        scores = []
        for req in required:
            for cap in self.capabilities:
                if cap.name == req:
                    scores.append(cap.proficiency)
                    break
            else:
                scores.append(0.0)

        return sum(scores) / len(required) if scores else 0.0


@dataclass
class Task:
    """A task to be executed by the swarm."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    required_capabilities: List[str] = field(default_factory=list)
    priority: TaskPriority = TaskPriority.NORMAL
    deadline: Optional[datetime] = None
    parameters: Dict = field(default_factory=dict)
    status: str = "pending"
    assigned_to: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def is_assigned(self) -> bool:
        """Check if task is assigned."""
        return self.assigned_to is not None


@dataclass
class Bid:
    """A bid from an agent for a task."""

    agent_id: str
    task_id: str
    score: float  # Bid score (higher is better)
    estimated_time: float = 0.0  # Estimated completion time
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def combined_score(self) -> float:
        """Combined bid score considering all factors."""
        return self.score * self.confidence


@dataclass
class SwarmMessage:
    """Message passed between swarm agents."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: MessageType = MessageType.HEARTBEAT
    sender_id: str = ""
    receiver_id: Optional[str] = None  # None = broadcast
    payload: Dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TeamFormation:
    """A dynamically formed team of agents."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    members: Set[str] = field(default_factory=set)
    leader_id: Optional[str] = None
    goal: str = ""
    status: str = "forming"
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def size(self) -> int:
        """Number of members in the team."""
        return len(self.members)

    def add_member(self, agent_id: str) -> None:
        """Add a member to the team."""
        self.members.add(agent_id)

    def remove_member(self, agent_id: str) -> None:
        """Remove a member from the team."""
        self.members.discard(agent_id)
        if self.leader_id == agent_id:
            self.leader_id = next(iter(self.members), None)


@dataclass
class Pheromone:
    """Virtual pheromone for indirect communication."""

    type: str  # e.g., "success", "help_needed", "avoid"
    location: str  # Context/topic identifier
    intensity: float = 1.0
    depositor_id: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    def decay(self, rate: float = 0.1) -> None:
        """Reduce intensity due to decay."""
        self.intensity = max(0.0, self.intensity - rate)

    @property
    def is_active(self) -> bool:
        """Check if pheromone is still active."""
        return self.intensity > 0.1
