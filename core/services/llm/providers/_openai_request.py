"""Request/response shaping for the OpenAI provider (pure, no client).

Two corrections live here:

* **Token cap.** Chat Completions moved to ``max_completion_tokens``; the
  reasoning models reject ``max_tokens`` outright, and the parameter is
  accepted universally now, so every call translates. Sending the old name was
  an HTTP 400 on exactly the models a caller is most likely to reach for.
* **Usage.** ``usage.total_tokens`` alone cannot be priced: cached prompt
  tokens are counted *inside* ``prompt_tokens`` at a fraction of the rate.
  :func:`extract_usage` splits them out, and keeps the reported total for the
  degraded case where a server sends nothing else.

Kept out of the provider module for the file-size cap; pure functions, so they
test without a client.
"""

from __future__ import annotations

from typing import Any

from core.services.llm.usage import Usage

__all__ = [
    "RESERVED_KWARGS",
    "extract_usage",
    "refusal_of",
    "request_kwargs",
    "token_cap",
]

#: Kwargs the provider interprets itself, or that belong to another provider's
#: surface. Chat Completions rejects unknown fields, so these must be stripped.
RESERVED_KWARGS = frozenset(
    {
        "system",
        "json_mode",
        "effort",
        "thinking_budget",
        "allow_refusal",
        "usage_sink",
        "betas",
        "max_tokens",
        "max_completion_tokens",
    }
)


def _coerce_int(value: Any) -> int:
    """Non-negative int, or 0 for ``None``/mocks/non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def token_cap(kwargs: dict[str, Any]) -> int:
    """The caller's output cap, under either spelling (0 when unset).

    Args:
        kwargs: Caller kwargs.

    Returns:
        int: The positive cap, or 0 when the caller set none.
    """
    return _coerce_int(kwargs.get("max_completion_tokens", kwargs.get("max_tokens")))


def request_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Forwardable kwargs, with the output cap under its current name.

    Args:
        kwargs: Caller kwargs.

    Returns:
        dict: Everything safe to pass to ``chat.completions.create``, with
        ``max_tokens`` (or an explicit ``max_completion_tokens``) rendered as
        ``max_completion_tokens``.
    """
    out = {k: v for k, v in kwargs.items() if k not in RESERVED_KWARGS}
    cap = token_cap(kwargs)
    if cap > 0:
        out["max_completion_tokens"] = cap
    return out


def extract_usage(response: Any) -> tuple[Usage, int]:
    """Split the response's usage, and keep the total it reported.

    Args:
        response: A chat completion (or chunk) carrying ``usage``.

    Returns:
        tuple: the metered :class:`Usage` (empty when the server sent no
        per-bucket counts) and ``usage.total_tokens`` (0 when absent).
    """
    raw = getattr(response, "usage", None)
    if raw is None:
        return Usage(), 0
    return Usage.from_openai(raw), _coerce_int(getattr(raw, "total_tokens", 0))


def refusal_of(message: Any) -> str | None:
    """The assistant's refusal text, when the model declined to answer.

    OpenAI reports a refusal as a dedicated field on the message rather than
    as a finish reason, so it has to be read separately to be mapped onto the
    same neutral stop reason as Anthropic's.
    """
    refusal = getattr(message, "refusal", None)
    return refusal if isinstance(refusal, str) and refusal.strip() else None
