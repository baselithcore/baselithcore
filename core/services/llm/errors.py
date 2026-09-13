"""Neutral LLM error taxonomy, and the SDK→neutral exception mapping.

Both provider SDKs raise a precise, typed exception for every failure mode —
and the stack threw all of it away, wrapping everything as
``LLMProviderError("<Provider> error: ...")`` and then deciding whether to
retry by looking for ``"429"`` or ``"rate limit"`` *inside the string*. Two
consequences, both expensive:

* a 500, a dropped connection and a read timeout — the three most common
  transient failures — were never retried, because their messages carry none
  of those words;
* a prompt that merely *mentions* "rate limit" retried a request that was
  never throttled.

These classes restore the distinction without leaking an SDK type into the
call sites: the providers map at the boundary, and the retry layers decide by
``isinstance``. The string heuristic survives only as a last resort for
exception types the mapping does not recognise.

Mapping is duck-typed rather than ``isinstance``-based against the SDKs: both
``anthropic`` and ``openai`` declare structurally identical hierarchies
(``APIStatusError.status_code``, ``APITimeoutError`` under
``APIConnectionError``), the SDKs are optional dependencies here, and unit
tests patch the SDK module with a mock — under which an ``isinstance`` check
would silently match nothing.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.services.llm.exceptions import (
    LLMProviderError,
    RateLimitError,
    describe_exception,
)

logger = get_logger(__name__)

__all__ = [
    "LLMClientError",
    "LLMConnectionError",
    "LLMRateLimitError",
    "LLMRefusalError",
    "LLMServerError",
    "LLMTimeoutError",
    "RETRYABLE_ERRORS",
    "is_retryable",
    "map_provider_exception",
    "retry_after_from_exception",
]

# Cap on a server-supplied Retry-After. A provider (or a proxy in front of it)
# can answer with a window far longer than any request is willing to wait;
# honouring it verbatim would pin a worker for minutes.
MAX_HONOURED_RETRY_AFTER_SECONDS = 120.0


class LLMRateLimitError(RateLimitError):
    """The provider throttled the request (HTTP 429).

    Subclasses the historical :class:`~core.services.llm.exceptions.RateLimitError`
    so the retry decorators already armed with that class keep firing, and so
    existing ``except RateLimitError`` call sites are unaffected.

    Attributes:
        retry_after: Seconds the provider asked us to wait, when it said.
        status_code: Always 429 unless the caller overrides it.
    """

    def __init__(
        self,
        *args: object,
        retry_after: float | None = None,
        status_code: int | None = 429,
    ) -> None:
        super().__init__(*args, retry_after=retry_after)
        self.status_code = status_code


class LLMServerError(LLMProviderError):
    """The provider failed on its own side (HTTP 5xx). Retryable.

    Attributes:
        status_code: The upstream status, when one was reported.
    """

    def __init__(self, *args: object, status_code: int | None = None) -> None:
        super().__init__(*args)
        self.status_code = status_code


class LLMConnectionError(LLMProviderError):
    """The request never reached the provider (DNS, TLS, reset). Retryable."""


class LLMTimeoutError(LLMConnectionError):
    """The provider did not answer within the request deadline. Retryable.

    Subclasses :class:`LLMConnectionError` exactly as both SDKs place
    ``APITimeoutError`` under ``APIConnectionError``: a timeout *is* a
    connection-level failure, and a handler written for the broader class must
    keep catching it.
    """


class LLMClientError(LLMProviderError):
    """The request itself was rejected (4xx other than 429). Not retryable.

    A bad key, an unknown model, a malformed payload — resending it produces
    the same answer while spending another attempt and another slot in the
    circuit breaker.

    Attributes:
        status_code: The upstream status, when one was reported.
    """

    def __init__(self, *args: object, status_code: int | None = None) -> None:
        super().__init__(*args)
        self.status_code = status_code


class LLMRefusalError(LLMProviderError):
    """The model declined to answer (``stop_reason == "refusal"``).

    Distinct from a content-filter *error*: the request succeeded and was
    billed, the model simply refused to produce the completion. Callers that
    want to inspect the refusal instead of failing pass ``allow_refusal=True``.

    Attributes:
        category: Provider-reported refusal category, when given.
        explanation: Provider-reported explanation, when given.
    """

    def __init__(
        self,
        *args: object,
        category: str | None = None,
        explanation: str | None = None,
    ) -> None:
        if not args:
            detail = ": ".join(part for part in (category, explanation) if part)
            args = (
                f"Model refused to answer ({detail})"
                if detail
                else "Model refused to answer",
            )
        super().__init__(*args)
        self.category = category
        self.explanation = explanation


#: Failures worth another attempt: the condition may not hold a moment later.
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    LLMRateLimitError,
    RateLimitError,
    LLMServerError,
    LLMConnectionError,
    LLMTimeoutError,
)

#: Substrings that identify a throttle in an exception type the mapping does
#: not know. Kept narrow on purpose — this is the heuristic that used to be
#: the *only* signal.
_RATE_LIMIT_MARKERS = ("429", "rate limit", "too many")


def is_retryable(exc: BaseException) -> bool:
    """Whether *exc* is worth retrying.

    Args:
        exc: The failure raised by a provider call.

    Returns:
        bool: True for the transient classes (rate limit, 5xx, connection,
        timeout). Unknown exception types fall back to the legacy substring
        heuristic so nothing that used to retry stops retrying.
    """
    if isinstance(exc, (LLMClientError, LLMRefusalError)):
        return False
    if isinstance(exc, RETRYABLE_ERRORS):
        return True
    # Unknown type, or the generic provider wrapper: fall back to the text.
    text = str(exc).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def retry_after_from_exception(exc: BaseException) -> float | None:
    """Extract the RFC 9110 ``Retry-After`` window from a provider exception.

    Provider SDKs surface the HTTP response on the raised error, so the header
    is reachable without depending on any one SDK's types: the lookup is
    duck-typed and every failure path returns ``None``, leaving the retry layer
    on its own backoff curve.

    Only the delta-seconds form is honoured. The HTTP-date form is valid per
    the RFC but rare from these APIs, and parsing it correctly needs the
    server's clock — a skewed one would produce a wildly wrong wait.

    Args:
        exc: The provider exception, typically a 429.

    Returns:
        float | None: Seconds to wait, or ``None`` when unavailable, absurd
        (beyond :data:`MAX_HONOURED_RETRY_AFTER_SECONDS`) or not a number.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        logger.debug("retry_after_header_unreadable", exc_info=True)
        return None
    if not raw:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None  # HTTP-date form, or malformed
    if seconds <= 0 or seconds > MAX_HONOURED_RETRY_AFTER_SECONDS:
        return None
    return seconds


def _class_names(exc: BaseException) -> frozenset[str]:
    """Every class name in *exc*'s MRO (SDK-agnostic type identification)."""
    return frozenset(cls.__name__ for cls in type(exc).__mro__)


def _status_code(exc: BaseException) -> int | None:
    """The upstream HTTP status carried by *exc*, when it is a real int."""
    status: Any = getattr(exc, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


def map_provider_exception(
    exc: BaseException, *, provider: str, action: str = ""
) -> LLMProviderError:
    """Translate an SDK exception into this package's neutral taxonomy.

    Args:
        exc: The exception raised by the provider SDK.
        provider: Human-readable provider label for the message
            (``"Anthropic"``, ``"OpenAI"``).
        action: Optional qualifier for the message (``"streaming"``).

    Returns:
        LLMProviderError: The most specific neutral class the exception maps
        to; a plain :class:`LLMProviderError` when it matches nothing known.
        Neutral errors are returned unchanged, so a mapping applied twice is a
        no-op.
    """
    if isinstance(exc, LLMProviderError):
        return exc

    label = f"{provider} {action}".strip()
    message = f"{label} error: {describe_exception(exc)}"
    names = _class_names(exc)

    # Timeout before connection: both SDKs nest APITimeoutError under
    # APIConnectionError, so the broader check would swallow it.
    if "APITimeoutError" in names or isinstance(exc, TimeoutError):
        return LLMTimeoutError(message)
    if "APIConnectionError" in names:
        return LLMConnectionError(message)

    status = _status_code(exc)
    if status == 429 or "RateLimitError" in names:
        return LLMRateLimitError(
            message,
            retry_after=retry_after_from_exception(exc),
            status_code=status or 429,
        )
    if status is not None:
        if status >= 500:
            return LLMServerError(message, status_code=status)
        if status >= 400:
            return LLMClientError(message, status_code=status)
    return LLMProviderError(message)
