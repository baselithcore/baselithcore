"""
Tests for core.services.llm modules.
"""


class TestLLMServiceImports:
    """Tests for LLM service imports."""

    def test_get_llm_service_import(self):
        """get_llm_service can be imported."""
        from core.services.llm import get_llm_service

        assert callable(get_llm_service)

    def test_llm_service_import(self):
        """LLMService can be imported."""
        from core.services.llm.service import LLMService

        assert LLMService is not None


class TestLLMExceptions:
    """Tests for LLM exceptions."""

    def test_budget_exceeded_error_import(self):
        """BudgetExceededError can be imported."""
        from core.services.llm.exceptions import BudgetExceededError

        assert BudgetExceededError is not None

    def test_budget_exceeded_is_exception(self):
        """BudgetExceededError is an Exception subclass."""
        from core.services.llm.exceptions import BudgetExceededError

        assert issubclass(BudgetExceededError, Exception)

    def test_llm_provider_error_import(self):
        """LLMProviderError can be imported."""
        from core.services.llm.exceptions import LLMProviderError

        assert LLMProviderError is not None


class TestCostControl:
    """Tests for cost control module."""

    def test_cost_tracker_import(self):
        """CostTracker can be imported."""
        from core.services.llm.cost_control import CostTracker

        assert CostTracker is not None


class TestOpenAIProvider:
    """Tests for OpenAI provider."""

    def test_openai_provider_import(self):
        """OpenAIProvider can be imported."""
        from core.services.llm.providers.openai_provider import OpenAIProvider

        assert OpenAIProvider is not None


class TestOllamaProvider:
    """Tests for Ollama provider."""

    def test_ollama_provider_import(self):
        """OllamaProvider can be imported."""
        from core.services.llm.providers.ollama_provider import OllamaProvider

        assert OllamaProvider is not None
