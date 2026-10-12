"""
Reflection Pattern Module

Provides self-evaluation and iterative refinement capabilities for LLM responses.
This module implements the Reflection agentic design pattern, enabling agents to:
- Evaluate the quality of their own responses
- Iteratively refine responses based on self-feedback
- Early exit when no further improvement is detected
"""

from .agent import ReflectionAgent
from .evaluators import DefaultEvaluator
from .protocols import EvaluationResult, Refiner, SelfEvaluator
from .refiners import DefaultRefiner

__all__ = [
    "DefaultEvaluator",
    "DefaultRefiner",
    "EvaluationResult",
    "Refiner",
    "ReflectionAgent",
    "SelfEvaluator",
]
