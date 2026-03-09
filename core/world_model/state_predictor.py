"""
State Predictor

Predicts future states based on actions and current state.
Uses LLM for intelligent prediction when available.
"""

from core.observability.logging import get_logger
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from core.services.llm import LLMService

from .types import State, Action, Transition

logger = get_logger(__name__)


class StatePredictor:
    """
    Predicts future states from current state and actions.

    Can use:
    - Rule-based prediction (using action effects)
    - LLM-based prediction (for complex scenarios)
    - Custom prediction functions

    Example:
        ```python
        predictor = StatePredictor()
        next_state = predictor.predict(current_state, action)
        ```
    """

    def __init__(
        self,
        llm_service: Optional["LLMService"] = None,
        config: Optional[Any] = None,  # WorldModelConfig
        custom_predictor: Optional[Callable[[State, Action], State]] = None,
    ):
        """
        Initialize state predictor.

        Args:
            llm_service: LLM Service instance
            config: WorldModelConfig instance
            custom_predictor: Custom prediction function
        """
        self.llm_service = llm_service
        self.config = config
        self.custom_predictor = custom_predictor

        # Lazy load LLM service if not provided
        if not self.llm_service:
            try:
                from core.services.llm import get_llm_service

                self.llm_service = get_llm_service()
            except ImportError:
                logger.warning("LLM service not available")

    async def predict(
        self,
        state: State,
        action: Action,
        context: Optional[Dict] = None,
    ) -> State:
        """
        Predict next state after applying action.

        Args:
            state: Current state
            action: Action to apply
            context: Optional context for prediction

        Returns:
            Predicted next state
        """
        # Check preconditions
        if not action.can_apply(state):
            logger.warning(f"Action {action.name} preconditions not met")
            return state.copy()

        # Use custom predictor if available
        if self.custom_predictor:
            return self.custom_predictor(state, action)

        # Prioritize explicit action effects if present
        if action.effects and not (context and context.get("force_llm")):
            return action.apply(state)

        # Use LLM for complex predictions or if explicitly requested
        if self.llm_service:
            # Logic to decide when to use LLM could be enhanced here via config
            return await self._predict_with_llm(state, action, context)

        # Default: apply action effects directly
        return action.apply(state)

    async def _predict_with_llm(
        self,
        state: State,
        action: Action,
        context: Optional[Dict],
    ) -> State:
        """Use LLM to predict complex state transitions."""
        prompt = f"""Predict the resulting state after an action.

Current State:
{self._format_state(state)}

Action: {action.name}
Type: {action.action_type.value}
Parameters: {action.parameters}
Description: {action.description}

Based on this action, what changes would occur in the state variables?
Provide changes in format:
VARIABLE: new_value
"""

        assert self.llm_service is not None  # nosec B101
        try:
            response = await self.llm_service.generate_response(prompt)
            return self._parse_predicted_state(state, response)
        except Exception as e:
            logger.error(f"LLM prediction failed: {e}")
            return action.apply(state)

    def _format_state(self, state: State) -> str:
        """Format state for LLM prompt."""
        lines = [f"Name: {state.name}"]
        for key, value in state.variables.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)

    def _parse_predicted_state(self, original: State, llm_response: str) -> State:
        """Parse LLM response into state updates."""
        new_state = original.copy()

        for line in llm_response.strip().split("\n"):
            if ":" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    raw_value = parts[1].strip()
                    # Try to convert to appropriate type
                    parsed_value: Any = raw_value
                    try:
                        if raw_value.lower() in ("true", "false"):
                            parsed_value = raw_value.lower() == "true"
                        elif raw_value.isdigit():
                            parsed_value = int(raw_value)
                        elif "." in raw_value and raw_value.replace(".", "").isdigit():
                            parsed_value = float(raw_value)
                    except Exception:
                        logger.debug(
                            f"State variable parsing failed for {key}={raw_value}"
                        )
                    new_state.variables[key] = parsed_value

        new_state.parent_id = original.id
        return new_state

    async def predict_sequence(
        self,
        state: State,
        actions: List[Action],
        context: Optional[Dict] = None,
    ) -> List[Transition]:
        """
        Predict state transitions for a sequence of actions.

        Args:
            state: Initial state
            actions: Sequence of actions
            context: Optional context

        Returns:
            List of transitions
        """
        transitions = []
        current_state = state

        for action in actions:
            next_state = await self.predict(current_state, action, context)
            transitions.append(
                Transition(
                    source_state=current_state,
                    action=action,
                    target_state=next_state,
                )
            )
            current_state = next_state

        return transitions

    async def compare_outcomes(
        self,
        state: State,
        actions: List[Action],
        context: Optional[Dict] = None,
    ) -> Dict[str, State]:
        """
        Compare outcomes of different actions from same state.

        Args:
            state: Initial state
            actions: Alternative actions to compare
            context: Optional context

        Returns:
            Dict mapping action names to resulting states
        """
        outcomes = {}

        for action in actions:
            result_state = await self.predict(state, action, context)
            outcomes[action.name] = result_state

        return outcomes
