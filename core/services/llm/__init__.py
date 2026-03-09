"""
LLM Service package.

Provides a modular, protocol-based LLM service with support for multiple providers.
"""

from core.services.llm.service import LLMService, get_llm_service
from core.services.llm.exceptions import BudgetExceededError

__all__ = [
    "LLMService",
    "get_llm_service",
    "BudgetExceededError",
]
