"""
Evaluation Service Module.

Provides LLM-as-a-Judge capabilities.
"""

from .service import EvaluationService, get_evaluation_service

__all__ = ["EvaluationService", "get_evaluation_service"]
