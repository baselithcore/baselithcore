"""
Predictive World Modeling via MCTS.

Implements Monte Carlo Tree Search (MCTS) to simulate future outcomes
of agent actions. Enables proactive decision-making by exploring
multiple branching futures and selecting paths that maximize expected
rewards.
"""

from core.observability.logging import get_logger
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any

from core.reasoning.mcts_common import uct_score as _uct_score, backpropagate_cumulative
from .types import State, Action, ActionPath, SimulationResult

logger = get_logger(__name__)


@dataclass
class MCTSNode:
    """
    Represents a single state in the Monte Carlo Tree Search.

    Tracks visit counts, accumulated rewards, and unexplored action
    space for a specific world state.

    Attributes:
        state: The world state represented by this node.
        action: The action that led to this state from the parent.
        parent: Reference to the parent node.
        children: List of child nodes representing subsequent states.
        visits: Number of times this node has been explored.
        total_reward: Cumulative reward value backpropagated to this node.
        untried_actions: List of actions from this state not yet expanded.
    """

    state: State
    action: Optional[Action] = None
    parent: Optional["MCTSNode"] = None
    children: List["MCTSNode"] = field(default_factory=list)
    visits: int = 0
    total_reward: float = 0.0
    untried_actions: List[Action] = field(default_factory=list)

    @property
    def is_fully_expanded(self) -> bool:
        """Check if all actions have been tried."""
        return len(self.untried_actions) == 0

    @property
    def is_terminal(self) -> bool:
        """Check if this is a terminal node."""
        return len(self.untried_actions) == 0 and len(self.children) == 0

    def ucb1(self, exploration: float = 1.41) -> float:
        """Calculate UCB1 value for node selection."""
        if self.visits == 0:
            return float("inf")
        avg_reward = self.total_reward / self.visits
        parent_visits = self.parent.visits if self.parent else 0
        return _uct_score(avg_reward, self.visits, parent_visits, exploration)

    def best_child(self, exploration: float = 1.41) -> Optional["MCTSNode"]:
        """Select best child using UCB1."""
        if not self.children:
            return None
        return max(self.children, key=lambda c: c.ucb1(exploration))


@dataclass
class MCTSConfig:
    """Configuration for MCTS."""

    max_iterations: int = 100
    max_depth: int = 10
    exploration_weight: float = 1.41
    simulation_depth: int = 5
    time_limit: float = 5.0  # seconds


class MCTSSimulator:
    """
    Predictive engine for future-aware planning.

    Utilizes selection, expansion, simulation (rollouts), and
    backpropagation to build a statistical model of action utilities. This
    allows the agent to perform 'what-if' analysis and avoid high-risk
    paths before committing to real-world execution.
    """

    def __init__(
        self,
        get_actions: Callable[[State], List[Action]],
        apply_action: Callable[[State, Action], State],
        reward_fn: Optional[Callable[[State], float]] = None,
        is_goal: Optional[Callable[[State], bool]] = None,
        config: Optional[Any] = None,  # WorldModelConfig
    ):
        """
        Initialize MCTS simulator.

        Args:
            get_actions: Function to get available actions for a state
            apply_action: Function to apply action and get next state
            reward_fn: Function to calculate reward for a state
            is_goal: Function to check if state is goal
            config: WorldModelConfig
        """
        self.get_actions = get_actions
        self.apply_action = apply_action
        self.reward_fn = reward_fn or (lambda s: 0.0)
        self.is_goal = is_goal or (lambda s: False)

        if config:
            self.config = config
        else:
            from core.config.world_model import get_world_model_config

            self.config = get_world_model_config()

        self._local_config: Any = None  # For what_if temporary override

    @property
    def current_config(self):
        """Return the active simulation configuration."""
        return self._local_config if self._local_config else self.config

    async def search(
        self,
        initial_state: State,
        context: Optional[Dict] = None,
    ) -> SimulationResult:
        """
        Execute the MCTS algorithm to discover the optimal action path.

        Performs iterative selection, expansion, simulation, and
        backpropagation until the maximum iteration count or time limit
        is reached.

        Args:
            initial_state: The starting world state for the simulation.
            context: Optional dictionary containing additional metadata or
                     constraints for the search.

        Returns:
            SimulationResult: A comprehensive report including the best
                             path found, performance metrics, and outcome
                             success flags.
        """
        start_time = time.time()

        cfg = self.current_config
        max_iterations = getattr(cfg, "mcts_max_iterations", 100)
        time_limit = getattr(cfg, "mcts_time_limit", 5.0)

        # Create root node
        root = MCTSNode(state=initial_state)
        root.untried_actions = self.get_actions(initial_state)

        iterations = 0
        all_paths = []

        while iterations < max_iterations:
            # Check time limit
            if time.time() - start_time > time_limit:
                break

            iterations += 1

            # 1. Selection
            node = self._select(root)

            # 2. Expansion
            if not node.is_terminal and not node.is_fully_expanded:
                node = self._expand(node)

            # 3. Simulation
            reward = await self._simulate(node)

            # 4. Backpropagation
            self._backpropagate(node, reward)

        # Extract best path
        best_path = self._extract_best_path(root)
        if best_path:
            all_paths.append(best_path)

        # Get final state
        final_state = self._get_final_state(root, best_path)

        computation_time = time.time() - start_time

        return SimulationResult(
            initial_state=initial_state,
            final_state=final_state,
            best_path=best_path,
            all_paths=all_paths,
            iterations=iterations,
            computation_time=computation_time,
            success=best_path is not None and len(best_path.actions) > 0,
            goal_reached=self.is_goal(final_state) if final_state else False,
        )

    def _select(self, node: MCTSNode) -> MCTSNode:
        """Select promising node to explore."""
        current: Optional[MCTSNode] = node
        depth = 0

        cfg = self.current_config
        max_depth = getattr(cfg, "mcts_max_depth", 10)
        exploration_weight = getattr(cfg, "mcts_exploration_weight", 1.41)

        while (
            current is not None
            and current.is_fully_expanded
            and current.children
            and depth < max_depth
        ):
            current = current.best_child(exploration_weight)
            depth += 1

        return current or node

    def _expand(self, node: MCTSNode) -> MCTSNode:
        """Expand node with new child."""
        if not node.untried_actions:
            return node

        # Pick random untried action
        action = random.choice(node.untried_actions)  # nosec B311
        node.untried_actions.remove(action)

        # Apply action to get new state
        # Note: apply_action is synchronous for MCTS usually (model based) but if it was async we'd need await
        # Keeping it sync here as per original design, but simulation is async
        new_state = self.apply_action(node.state, action)

        # Create child node
        child = MCTSNode(
            state=new_state,
            action=action,
            parent=node,
            untried_actions=self.get_actions(new_state),
        )
        node.children.append(child)

        return child

    async def _simulate(self, node: MCTSNode) -> float:
        """Run random simulation from node."""
        current_state = node.state
        total_reward = self.reward_fn(current_state)

        cfg = self.current_config
        simulation_depth = getattr(cfg, "mcts_simulation_depth", 5)

        for _ in range(simulation_depth):
            # Check if goal reached
            if self.is_goal(current_state):
                total_reward += 10.0  # Goal bonus
                break

            # Get available actions
            actions = self.get_actions(current_state)
            if not actions:
                break

            # Random action selection
            action = random.choice(actions)  # nosec B311
            current_state = self.apply_action(current_state, action)
            total_reward += self.reward_fn(current_state)

        return total_reward

    def _backpropagate(self, node: MCTSNode, reward: float) -> None:
        """Backpropagate reward through tree."""
        backpropagate_cumulative(node, reward)

    def _extract_best_path(self, root: MCTSNode) -> Optional[ActionPath]:
        """Extract best path from root."""
        path = ActionPath()
        current = root

        while current.children:
            # Select most visited child
            best_child = max(current.children, key=lambda c: c.visits)
            if best_child.action:
                path.add_action(best_child.action)
            current = best_child

        if path.length == 0:
            return None

        path.total_reward = root.total_reward / max(root.visits, 1)
        path.probability = 1.0

        return path

    def _get_final_state(
        self,
        root: MCTSNode,
        path: Optional[ActionPath],
    ) -> Optional[State]:
        """Get final state after following best path."""
        if not path:
            return root.state

        current = root.state
        for action in path.actions:
            current = self.apply_action(current, action)

        return current

    async def what_if(
        self,
        state: State,
        action: Action,
        depth: int = 3,
    ) -> SimulationResult:
        """
        Analyze the potential long-term consequences of a specific action.

        Forces an initial step with the provided action and then
        delegates to the standard MCTS search to explore the resulting
        branching future.

        Args:
            state: The current world state.
            action: The specific action to evaluate.
            depth: Look-ahead depth for the simulation.

        Returns:
            SimulationResult: Analysis of the future state and reward
                             trajectory for the given action.
        """
        # Apply the action first
        next_state = self.apply_action(state, action)

        # Temporary config override
        # We need to Create a temporary config object that mimics WorldModelConfig but with overrides.

        from dataclasses import dataclass

        @dataclass
        class TempConfig:
            mcts_max_iterations: int
            mcts_max_depth: int
            mcts_simulation_depth: int
            mcts_time_limit: float
            mcts_exploration_weight: float

        self._local_config = TempConfig(
            mcts_max_iterations=min(
                50, getattr(self.config, "mcts_max_iterations", 100)
            ),
            mcts_max_depth=depth,
            mcts_simulation_depth=depth,
            mcts_time_limit=getattr(self.config, "mcts_time_limit", 5.0),
            mcts_exploration_weight=getattr(
                self.config, "mcts_exploration_weight", 1.41
            ),
        )

        try:
            result = await self.search(next_state)
        finally:
            self._local_config = None

        # Prepend the original action
        if result.best_path:
            result.best_path.actions.insert(0, action)
        else:
            result.best_path = ActionPath(actions=[action])

        result.initial_state = state

        return result
