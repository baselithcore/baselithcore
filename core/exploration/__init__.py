"""
Exploration Module

Provides proactive exploration and discovery capabilities:
- Autonomous information gathering
- Hypothesis generation for unknowns
- Knowledge gap identification
"""

from .explorer import ProactiveExplorer, ExplorationResult
from .hypothesis import HypothesisGenerator, Hypothesis

__all__ = [
    "ProactiveExplorer",
    "ExplorationResult",
    "HypothesisGenerator",
    "Hypothesis",
]
