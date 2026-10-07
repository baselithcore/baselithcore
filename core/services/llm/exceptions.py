"""
LLM service exceptions.
"""

from __future__ import annotations


class BudgetExceededError(Exception):
    """Raised when token budget is exceeded."""

    pass


class LLMProviderError(Exception):
    """Raised when there's an error with the LLM provider."""

    pass


class StreamInterruptedError(LLMProviderError):
    """A provider stream failed *after* text had already reached the caller.

    Never retried and never failed over: the consumer has rendered the partial
    answer, so restarting on the same or another provider would duplicate or
    contradict it. The text that was delivered is kept on ``partial_text`` so a
    host can persist it as an interrupted answer.

    Attributes:
        partial_text: Everything streamed to the caller before the failure.
    """

    def __init__(self, message: str, *, partial_text: str = "") -> None:
        super().__init__(message)
        self.partial_text = partial_text


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


def describe_exception(exc: BaseException) -> str:
    """A human-usable description of *exc*, never an empty string.

    Providers wrap upstream failures as ``f"<Provider> error: {exc}"``. That is
    fine for an SDK error carrying a message, and useless for the ones that do
    not: every ``httpx`` timeout class (``ReadTimeout``, ``ConnectTimeout``,
    ``PoolTimeout``) stringifies to ``""`` when raised without arguments, which
    is exactly what a hung local model server produces. The operator then sees
    ``Ollama error:`` — a failure with no cause, no endpoint and nothing to act
    on, for the single most common way a self-hosted provider fails.

    Falling back to the exception's type name keeps the message short and
    always answers "what went wrong", which is the minimum a log line owes a
    reader at 3am.

    Args:
        exc: The upstream exception being wrapped.

    Credentials an upstream error echoes back (bearer tokens, ``api_key=``
    pairs, JWTs) are masked with the shared
    :func:`core.observability.redaction.redact_secrets`, so the text is safe
    to log and to surface.

    Returns:
        ``str(exc)`` (credentials masked) when it carries anything, else the
        exception's class name.
    """
    # Imported lazily: ``core.observability`` pulls in config and telemetry,
    # which must not load just because an exception class was imported.
    from core.observability.redaction import redact_secrets

    message = redact_secrets(str(exc)).strip()
    return message or type(exc).__name__
