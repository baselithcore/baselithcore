"""
LLM Service package.

Provides a modular, protocol-based LLM service with support for multiple providers.
"""

from core.services.llm.exceptions import BudgetExceededError
from core.services.llm.service import LLMService, get_llm_service

__all__ = [
    "BudgetExceededError",
    "LLMService",
    "get_llm_service",
]
