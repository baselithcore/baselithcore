"""Population-based evolutionary search (GEPA/AlphaEvolve-style).

Candidate archive with a per-instance Pareto frontier, bounded reflective
mutation, and a budgeted evolution loop with a holdout anti-gaming audit.
"""

from core.optimization.evolution.archive import CandidateArchive
from core.optimization.evolution.evolve import (
    Evaluator,
    EvolutionEngine,
    EvolutionReport,
    Mutator,
)
from core.optimization.evolution.mutation import ReflectiveMutator
from core.optimization.evolution.types import Candidate, EvolutionBudget

__all__ = [
    "Candidate",
    "CandidateArchive",
    "Evaluator",
    "EvolutionBudget",
    "EvolutionEngine",
    "EvolutionReport",
    "Mutator",
    "ReflectiveMutator",
]
