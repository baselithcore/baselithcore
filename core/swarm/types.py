"""
Fundamental Types for Swarm Intelligence.

Defines the core domain model for decentralized multi-agent systems,
including agent profiles, task definitions, and communication schema.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


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
    HANDOFF = "handoff"


@dataclass
class Capability:
    """An agent capability."""

    name: str
    proficiency: float = 1.0  # 0.0 to 1.0
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentProfile:
    """Profile of an agent in the swarm."""

    id: str
    name: str
    capabilities: list[Capability] = field(default_factory=list)
    status: AgentStatus = AgentStatus.IDLE
    current_load: float = 0.0  # 0.0 to 1.0
    success_rate: float = 1.0
    metadata: dict = field(default_factory=dict)

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

    def get_capability_score(self, required: list[str]) -> float:
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
    required_capabilities: list[str] = field(default_factory=list)
    priority: TaskPriority = TaskPriority.NORMAL
    deadline: datetime | None = None
    parameters: dict = field(default_factory=dict)
    context_requirements: dict[str, Any] = field(
        default_factory=dict
    )  # Memory context filters
    status: str = "pending"
    assigned_to: str | None = None
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
    receiver_id: str | None = None  # None = broadcast
    payload: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TeamFormation:
    """A dynamically formed team of agents."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    members: set[str] = field(default_factory=set)
    leader_id: str | None = None
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
class HandoffBrief:
    """Structured summary passed across a handoff boundary.

    Compressing what the sender learned into objective / facts / attempts /
    constraints beats shipping the raw accumulated context: the receiver gets
    exactly what it needs to continue (including what already failed, so it
    isn't retried), in a bounded, prompt-ready form.
    """

    objective: str = ""
    facts: list[str] = field(default_factory=list)
    attempted: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Render the brief as a compact Markdown block for prompts."""
        parts: list[str] = []
        if self.objective:
            parts.append(f"**Objective:** {self.objective}")
        for title, items in (
            ("Facts established", self.facts),
            ("Already attempted (do not retry blindly)", self.attempted),
            ("Constraints", self.constraints),
        ):
            if items:
                bullet_lines = "\n".join(f"- {item}" for item in items)
                parts.append(f"**{title}:**\n{bullet_lines}")
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "facts": list(self.facts),
            "attempted": list(self.attempted),
            "constraints": list(self.constraints),
        }


def compress_handoff_context(
    context: dict[str, Any],
    *,
    max_value_chars: int = 2000,
) -> dict[str, Any]:
    """Bound the raw context payload carried across a handoff.

    Deterministic (no LLM call): oversized string values are truncated with an
    explicit marker so the receiver knows content was elided. Non-string
    values pass through untouched.
    """
    compressed: dict[str, Any] = {}
    for key, value in context.items():
        if isinstance(value, str) and len(value) > max_value_chars:
            omitted = len(value) - max_value_chars
            compressed[key] = (
                f"{value[:max_value_chars]}… [truncated {omitted} chars at handoff]"
            )
        else:
            compressed[key] = value
    return compressed


@dataclass
class Handoff:
    """A structured transfer of a task from one agent to another.

    Unlike a bare reassignment, a handoff carries an explicit ``reason``, a
    ``context`` payload (accumulated state, partial results, constraints) and
    an optional :class:`HandoffBrief` summary so the receiving agent starts
    with what the sender learned rather than from scratch.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    from_agent: str = ""
    to_agent: str = ""
    reason: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    brief: HandoffBrief | None = None
    hop_count: int = 0
    visited: tuple[str, ...] = ()
    timestamp: datetime = field(default_factory=datetime.now)

    def to_message(self) -> "SwarmMessage":
        """Render the handoff as a directed :class:`SwarmMessage`."""
        payload: dict[str, Any] = {
            "handoff_id": self.id,
            "task_id": self.task_id,
            "reason": self.reason,
            "context": self.context,
        }
        if self.brief is not None:
            payload["brief"] = self.brief.to_dict()
        return SwarmMessage(
            type=MessageType.HANDOFF,
            sender_id=self.from_agent,
            receiver_id=self.to_agent,
            payload=payload,
        )


class HandoffCycleGuard:
    """Refuses handoffs that ping-pong a task or exceed a hop cap.

    Tracks, per task, the ordered chain of agents that have held it. A
    handoff is refused when the recipient already appears in that chain
    (A→B→A ping-pong) or when the chain has reached ``max_hops``. Without
    this guard, a cycling handoff only stops when the ambient loop budget
    dies — burning the whole budget on routing instead of work.
    """

    def __init__(self, *, enabled: bool = True, max_hops: int = 8) -> None:
        self._enabled = enabled
        self._max_hops = max_hops
        self._trails: dict[str, list[str]] = {}

    def check(self, task_id: str, from_agent: str, to_agent: str) -> str | None:
        """Return a refusal reason for this transfer, or None if allowed."""
        if not self._enabled:
            return None
        trail = self._trails.get(task_id, [])
        if len(trail) >= self._max_hops:
            return f"max_hops ({self._max_hops}) reached"
        if to_agent in trail or to_agent == from_agent:
            return f"recipient {to_agent} already held the task"
        return None

    def record(self, task_id: str, from_agent: str) -> tuple[int, tuple[str, ...]]:
        """Append ``from_agent`` to the task's trail after an accepted handoff.

        Returns:
            ``(hop_count, visited)`` — the 1-based hop number of this
            transfer and the chain of prior holders, for the Handoff record.
        """
        trail = self._trails.setdefault(task_id, [])
        trail.append(from_agent)
        return len(trail), tuple(trail)


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
