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

    Retryable. When the provider told us *how long* to wait — the RFC 9110
    ``Retry-After`` header — that value is carried on ``retry_after`` so the
    retry layer can honour it instead of guessing with exponential backoff.
    Guessing shorter than the provider's window re-sends into a closed door
    and deepens the throttle; guessing longer wastes the request budget.

    Attributes:
        retry_after: Seconds the provider asked us to wait, or ``None`` when it
            did not say.
    """

    def __init__(self, *args: object, retry_after: float | None = None) -> None:
        super().__init__(*args)
        self.retry_after = retry_after
