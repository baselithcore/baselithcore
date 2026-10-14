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

import re
from typing import Any

from baselith.core.services.llm.usage import Usage

__all__ = [
    "RESERVED_KWARGS",
    "apply_reasoning_defaults",
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


#: Families that predate reasoning models; Chat Completions answers 400
#: ("Unrecognized request argument") when sent ``reasoning_effort``.
_NON_REASONING = re.compile(r"^(ft:)?(gpt-[34]|chatgpt)", re.IGNORECASE)

#: Reasoning families; they answer 400 to any ``temperature`` but the default 1.
#: Matched positively so an unknown model behind a compatible endpoint keeps
#: the temperature its caller chose.
_REASONING = re.compile(r"^(ft:)?(o\d|gpt-[5-9])", re.IGNORECASE)


def apply_reasoning_defaults(
    request: dict[str, Any], model: str, reasoning_effort: str | None
) -> dict[str, Any]:
    """Fit *request* to what *model* accepts, in place.

    Args:
        request: The Chat Completions request being built.
        model: Target model id.
        reasoning_effort: Effort to send; skipped for non-reasoning families.

    Returns:
        dict: *request*, with ``reasoning_effort`` set where the model takes it
        and a non-default ``temperature`` dropped for reasoning models.
    """
    if reasoning_effort and not _NON_REASONING.match(model):
        request.setdefault("reasoning_effort", reasoning_effort)
    if _REASONING.match(model) and request.get("temperature", 1) != 1:
        del request["temperature"]
    return request


def request_kwargs(
    kwargs: dict[str, Any],
    reasoning_effort: str | None = None,
    model: str = "",
) -> dict[str, Any]:
    """Forwardable kwargs, with the output cap under its current name.

    Args:
        kwargs: Caller kwargs.
        reasoning_effort: Provider-level default for the ``reasoning_effort``
            request field (``none``/``low``/...). A caller-supplied
            ``reasoning_effort`` wins; ``None`` sends nothing. Not applied to
            non-reasoning families (gpt-3/gpt-4/chatgpt).
        model: Target model id, used to skip the default where it is rejected.

    Returns:
        dict: Everything safe to pass to ``chat.completions.create``, with
        ``max_tokens`` (or an explicit ``max_completion_tokens``) rendered as
        ``max_completion_tokens``.
    """
    out = {k: v for k, v in kwargs.items() if k not in RESERVED_KWARGS}
    cap = token_cap(kwargs)
    if cap > 0:
        out["max_completion_tokens"] = cap
    if "reasoning_effort" in out:
        reasoning_effort = None  # the caller's own value goes through as given
    return apply_reasoning_defaults(out, model, reasoning_effort)


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
