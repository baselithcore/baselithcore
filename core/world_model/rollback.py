"""
Rollback Planner

Generates rollback plans for actions and sequences.
"""

from core.observability.logging import get_logger
from typing import Dict, List, Optional, Any

from .types import State, Action, ActionType, RollbackPlan, RiskLevel

logger = get_logger(__name__)


class RollbackPlanner:
    """
    Plans rollback strategies for actions.

    Generates:
    - Single action rollbacks
    - Sequence rollbacks (reverse order)
    - Checkpoint-based rollbacks

    Example:
        ```python
        planner = RollbackPlanner()
        plan = planner.create_rollback(action, current_state)
        if plan.can_rollback:
            for rollback_action in plan.rollback_actions:
                execute(rollback_action)
        ```
    """

    # Action type inverse mappings
    INVERSE_OPERATIONS = {
        ActionType.CREATE: ActionType.DELETE,
        ActionType.DELETE: ActionType.CREATE,
        ActionType.UPDATE: ActionType.UPDATE,  # Restore previous value
    }

    def __init__(
        self,
        config: Optional[Any] = None,  # WorldModelConfig
    ):
        """
        Initialize rollback planner.

        Args:
            config: WorldModelConfig instance
        """
        if config:
            self.enable_checkpoints = config.rollback_enable_checkpoints
            self.max_checkpoint_age = config.rollback_max_checkpoint_age
        else:
            from core.config.world_model import get_world_model_config

            config = get_world_model_config()
            self.enable_checkpoints = config.rollback_enable_checkpoints
            self.max_checkpoint_age = config.rollback_max_checkpoint_age

        # Checkpoint storage
        self._checkpoints: Dict[str, State] = {}
        self._action_history: List[tuple] = []  # (action, state_before)

    def record_action(
        self,
        action: Action,
        state_before: State,
    ) -> None:
        """
        Record action execution for potential rollback.

        Args:
            action: Executed action
            state_before: State before execution
        """
        self._action_history.append((action, state_before))

        # Create checkpoint if needed
        if self.enable_checkpoints and len(self._action_history) % 5 == 0:
            checkpoint_id = f"checkpoint_{len(self._action_history)}"
            self._checkpoints[checkpoint_id] = state_before.copy()

    def create_rollback(
        self,
        action: Action,
        state_before: State,
    ) -> RollbackPlan:
        """
        Create rollback plan for a single action.

        Args:
            action: Action to create rollback for
            state_before: State before action

        Returns:
            RollbackPlan
        """
        if not action.reversible:
            return RollbackPlan(
                original_action=action,
                rollback_actions=[],
                checkpoint_state=state_before.copy(),
                feasibility=0.0,
            )

        rollback_actions = self._generate_inverse_action(action, state_before)
        feasibility = self._calculate_feasibility(action, rollback_actions)

        return RollbackPlan(
            original_action=action,
            rollback_actions=rollback_actions,
            checkpoint_state=state_before.copy(),
            estimated_cost=sum(a.cost for a in rollback_actions),
            feasibility=feasibility,
        )

    def create_sequence_rollback(
        self,
        actions: List[Action],
        initial_state: State,
    ) -> List[RollbackPlan]:
        """
        Create rollback plans for action sequence.

        Returns plans in reverse order for proper undo.

        Args:
            actions: Actions to create rollbacks for
            initial_state: State before first action

        Returns:
            List of RollbackPlans (reversed)
        """
        plans = []
        current_state = initial_state

        for action in actions:
            plan = self.create_rollback(action, current_state)
            plans.append(plan)
            # Simulate state change
            current_state = action.apply(current_state)

        # Return in reverse order
        return list(reversed(plans))

    def _generate_inverse_action(
        self,
        action: Action,
        state_before: State,
    ) -> List[Action]:
        """Generate inverse action(s) to undo original."""

        # For updates, restore previous values
        if action.action_type == ActionType.UPDATE:
            restore_effects = {}
            for key in action.effects:
                original_value = state_before.get(key)
                if original_value is not None:
                    restore_effects[key] = original_value

            if restore_effects:
                return [
                    Action(
                        name=f"rollback_{action.name}",
                        action_type=ActionType.UPDATE,
                        effects=restore_effects,
                        description=f"Rollback: restore state before {action.name}",
                        cost=action.cost,
                        risk_level=RiskLevel.LOW,
                        reversible=True,
                    )
                ]

        # For create/delete, invert
        if action.action_type == ActionType.CREATE:
            return [
                Action(
                    name=f"rollback_{action.name}",
                    action_type=ActionType.DELETE,
                    parameters=action.parameters.copy(),
                    description=f"Rollback: delete created by {action.name}",
                    cost=action.cost,
                    risk_level=RiskLevel.MEDIUM,
                    reversible=True,
                )
            ]

        if action.action_type == ActionType.DELETE:
            # Need to recreate - more complex
            return [
                Action(
                    name=f"rollback_{action.name}",
                    action_type=ActionType.CREATE,
                    parameters=action.parameters.copy(),
                    effects=dict(state_before.variables),  # Restore all
                    description=f"Rollback: recreate deleted by {action.name}",
                    cost=action.cost * 2,  # More expensive
                    risk_level=RiskLevel.HIGH,
                    reversible=True,
                )
            ]

        return []

    def _calculate_feasibility(
        self,
        original: Action,
        rollback_actions: List[Action],
    ) -> float:
        """Calculate rollback feasibility score."""
        if not rollback_actions:
            return 0.0

        if not original.reversible:
            return 0.0

        # Factor in risk levels
        total_risk = sum(a.risk_level.value for a in rollback_actions)
        avg_risk = total_risk / len(rollback_actions)

        # Lower risk = higher feasibility
        # Scale risk (1-5) to penalty (0.1-0.5)
        risk_penalty = avg_risk / 10.0
        feasibility = 1.0 - risk_penalty

        # Factor in cost
        total_cost = sum(a.cost for a in rollback_actions)
        if total_cost > original.cost * 3:
            feasibility *= 0.7

        return max(0.0, min(1.0, feasibility))

    def get_nearest_checkpoint(
        self,
        target_actions_ago: int = 0,
    ) -> Optional[State]:
        """
        Get nearest checkpoint state.

        Args:
            target_actions_ago: How many actions back to look

        Returns:
            Checkpoint state if available
        """
        if not self._checkpoints:
            return None

        # Find best checkpoint
        best_key = None
        best_distance = float("inf")
        current_pos = len(self._action_history)

        for key in self._checkpoints:
            try:
                checkpoint_pos = int(key.split("_")[1])
                distance = abs(current_pos - target_actions_ago - checkpoint_pos)
                if distance < best_distance:
                    best_distance = distance
                    best_key = key
            except (ValueError, IndexError):
                continue

        return self._checkpoints.get(best_key) if best_key else None

    def clear_history(self) -> None:
        """Clear action history and checkpoints."""
        self._action_history.clear()
        self._checkpoints.clear()

    def get_history_length(self) -> int:
        """Get number of recorded actions."""
        return len(self._action_history)
