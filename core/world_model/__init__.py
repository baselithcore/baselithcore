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

from .types import State, Action, Transition, SimulationResult, RiskLevel
from .state_predictor import StatePredictor
from .simulation import MCTSSimulator
from .risk_assessor import RiskAssessor
from .rollback import RollbackPlanner

__all__ = [
    "State",
    "Action",
    "Transition",
    "SimulationResult",
    "RiskLevel",
    "StatePredictor",
    "MCTSSimulator",
    "RiskAssessor",
    "RollbackPlanner",
]
