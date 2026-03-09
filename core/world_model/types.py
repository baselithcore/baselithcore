"""
World Model Types

Core data structures for predictive planning.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid


class RiskLevel(Enum):
    """Risk level of an action or plan."""

    MINIMAL = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    CRITICAL = 5


class ActionType(Enum):
    """Types of actions."""

    QUERY = "query"
    EXECUTE = "execute"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    COMMUNICATE = "communicate"
    WAIT = "wait"


@dataclass
class State:
    """
    Represents a world state at a point in time.

    A state captures all relevant information about the
    current context, enabling prediction of future states.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    variables: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    parent_id: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Get variable value."""
        return self.variables.get(key, default)

    def set(self, key: str, value: Any) -> "State":
        """Create new state with updated variable."""
        new_vars = self.variables.copy()
        new_vars[key] = value
        return State(
            name=self.name,
            variables=new_vars,
            parent_id=self.id,
            metadata=self.metadata.copy(),
        )

    def copy(self) -> "State":
        """Create a copy of this state."""
        return State(
            name=self.name,
            variables=self.variables.copy(),
            parent_id=self.parent_id,
            metadata=self.metadata.copy(),
        )

    def diff(self, other: "State") -> Dict[str, tuple]:
        """Get differences between states."""
        diffs = {}
        all_keys = set(self.variables.keys()) | set(other.variables.keys())

        for key in all_keys:
            v1 = self.variables.get(key)
            v2 = other.variables.get(key)
            if v1 != v2:
                diffs[key] = (v1, v2)

        return diffs


@dataclass
class Action:
    """
    An action that can be taken to transition between states.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    action_type: ActionType = ActionType.EXECUTE
    parameters: Dict[str, Any] = field(default_factory=dict)
    preconditions: Dict[str, Any] = field(default_factory=dict)
    effects: Dict[str, Any] = field(default_factory=dict)
    cost: float = 1.0
    risk_level: RiskLevel = RiskLevel.LOW
    reversible: bool = True
    description: str = ""

    def can_apply(self, state: State) -> bool:
        """Check if action can be applied to state."""
        for key, expected in self.preconditions.items():
            if state.get(key) != expected:
                return False
        return True

    def apply(self, state: State) -> State:
        """Apply action effects to create new state."""
        new_state = state.copy()
        for key, value in self.effects.items():
            new_state.variables[key] = value
        new_state.parent_id = state.id
        return new_state


@dataclass
class Transition:
    """A transition from one state to another via an action."""

    source_state: State
    action: Action
    target_state: State
    probability: float = 1.0
    reward: float = 0.0
    metadata: Dict = field(default_factory=dict)


@dataclass
class ActionPath:
    """A sequence of actions forming a path."""

    actions: List[Action] = field(default_factory=list)
    total_cost: float = 0.0
    total_reward: float = 0.0
    risk_score: float = 0.0
    probability: float = 1.0

    @property
    def length(self) -> int:
        """Number of actions in path."""
        return len(self.actions)

    def add_action(self, action: Action, reward: float = 0.0) -> None:
        """Add action to path."""
        self.actions.append(action)
        self.total_cost += action.cost
        self.total_reward += reward
        # Accumulate risk
        self.risk_score = max(self.risk_score, action.risk_level.value)


@dataclass
class SimulationResult:
    """Result of a simulation run."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    initial_state: Optional[State] = None
    final_state: Optional[State] = None
    best_path: Optional[ActionPath] = None
    all_paths: List[ActionPath] = field(default_factory=list)
    iterations: int = 0
    computation_time: float = 0.0
    success: bool = False
    goal_reached: bool = False
    metadata: Dict = field(default_factory=dict)

    @property
    def best_reward(self) -> float:
        """Best achieved reward."""
        return self.best_path.total_reward if self.best_path else 0.0

    @property
    def explored_paths(self) -> int:
        """Number of paths explored."""
        return len(self.all_paths)


@dataclass
class RollbackPlan:
    """Plan for rolling back an action or sequence."""

    original_action: Action
    rollback_actions: List[Action] = field(default_factory=list)
    checkpoint_state: Optional[State] = None
    estimated_cost: float = 0.0
    feasibility: float = 1.0  # 0 to 1

    @property
    def can_rollback(self) -> bool:
        """Check if rollback is feasible."""
        return self.feasibility > 0.5 and len(self.rollback_actions) > 0
