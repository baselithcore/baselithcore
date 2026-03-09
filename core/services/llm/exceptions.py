"""
LLM service exceptions.
"""


class BudgetExceededError(Exception):
    """Raised when token budget is exceeded."""

    pass


class LLMProviderError(Exception):
    """Raised when there's an error with the LLM provider."""

    pass


class RateLimitError(LLMProviderError):
    """Raised when API rate limit is exceeded (429 status).

    This exception is retryable with exponential backoff.
    """

    pass
