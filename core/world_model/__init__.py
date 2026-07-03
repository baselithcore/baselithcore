"""
World Model Module

Provides predictive planning capabilities for agents.
Enables "what-if" analysis and simulation of future states
before taking actions.

Key Concepts:
- State: Representation of current world/context
- Action: Possible action an agent can take
- Transition: State -> Action -> Next State
- Simulation: Monte Carlo exploration of action paths
"""

from .risk_assessor import RiskAssessor
from .rollback import RollbackPlanner
from .simulation import MCTSSimulator
from .state_predictor import StatePredictor
from .types import Action, RiskLevel, SimulationResult, State, Transition

__all__ = [
    "Action",
    "MCTSSimulator",
    "RiskAssessor",
    "RiskLevel",
    "RollbackPlanner",
    "SimulationResult",
    "State",
    "StatePredictor",
    "Transition",
]
