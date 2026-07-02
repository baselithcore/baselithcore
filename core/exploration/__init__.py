"""
Exploration Module

Provides proactive exploration and discovery capabilities:
- Autonomous information gathering
- Hypothesis generation for unknowns
- Knowledge gap identification
"""

from .explorer import ExplorationResult, ProactiveExplorer
from .hypothesis import Hypothesis, HypothesisGenerator

__all__ = [
    "ExplorationResult",
    "Hypothesis",
    "HypothesisGenerator",
    "ProactiveExplorer",
]
