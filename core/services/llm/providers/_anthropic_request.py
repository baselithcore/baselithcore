"""Request shaping for the Anthropic provider (pure, no client dependency).

Three decisions used to be hard-coded identically in four provider methods,
and all three are now per-model:

* **Sampling.** ``temperature=0.7`` was injected on every call. On Fable 5.x,
  Mythos 5.x, Opus 5/4.8/4.7 and Sonnet 5 that is an HTTP 400 — those families
  removed ``temperature``/``top_p``/``top_k`` entirely. The parameter is now
  forwarded only when the caller asked for it *and* the family accepts it.
* **Output cap.** ``max_tokens=4096`` was the default everywhere, a third of
  the recommended buffered cap and one sixteenth of the streaming one, so long
  answers were silently truncated at the default.
* **Thinking.** The payload shape differs per family; see
  :mod:`core.services.llm.thinking`.

Keeping this here (like ``_anthropic_mapping``) leaves the provider module
under the file-size cap and makes the rules testable without a client.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.services.llm.model_capabilities import (
    capabilities_for,
    clamp_effort,
    default_max_tokens,
)
from core.services.llm.thinking import resolve_thinking
from core.services.llm.tool_calling import ToolChoice

logger = get_logger(__name__)

__all__ = [
    "RESERVED_KWARGS",
    "SAMPLING_PARAMS",
    "build_request_kwargs",
    "forwardable_kwargs",
    "merge_output_config",
    "resolve_max_tokens",
    "resolve_tool_choice",
    "sampling_kwargs",
]

#: Sampling parameters the newest families reject outright.
SAMPLING_PARAMS: tuple[str, ...] = ("temperature", "top_p", "top_k")

#: Kwargs this provider interprets itself; never forwarded verbatim to the SDK.
RESERVED_KWARGS = frozenset(
    {
        "max_tokens",
        "system",
        "thinking",
        "output_config",
        "effort",
        "thinking_budget",
        "betas",
        "extra_headers",
        "extra_body",
        "allow_refusal",
        "usage_sink",
        # OpenAI-style ``seed`` (deterministic mode) has no Messages API
        # counterpart; forwarding it fails the call with a TypeError, which on
        # a fallback chain whose primary is not Anthropic would sink the step.
        "seed",
        *SAMPLING_PARAMS,
    }
)


def resolve_max_tokens(
    model: str, kwargs: dict[str, Any], *, streaming: bool = False
) -> int:
    """The output cap for this call: the caller's, else the family default.

    Args:
        model: Target model id.
        kwargs: Caller kwargs (``max_tokens`` read when present).
        streaming: True for a streaming request, which supports a much larger
            cap than a buffered one.

    Returns:
        int: The ``max_tokens`` value to send.
    """
    value = kwargs.get("max_tokens")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default_max_tokens(model, streaming=streaming)


def sampling_kwargs(model: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Sampling parameters to forward, filtered by what *model* accepts.

    Only parameters the caller passed explicitly are considered — an injected
    default would override the model's own tuning for every caller that never
    asked for one — and they are dropped (with a debug line) on families that
    reject them, because sending one fails the whole request.

    Args:
        model: Target model id.
        kwargs: Caller kwargs.

    Returns:
        dict: The subset to include in the request.
    """
    allowed = capabilities_for(model).supports_sampling_params
    out: dict[str, Any] = {}
    for name in SAMPLING_PARAMS:
        value = kwargs.get(name)
        if value is None:
            continue
        if not allowed:
            logger.debug(
                "anthropic_sampling_param_dropped",
                extra={"model": model, "param": name},
            )
            continue
        out[name] = value
    return out


def build_request_kwargs(
    model: str, kwargs: dict[str, Any], *, streaming: bool = False
) -> dict[str, Any]:
    """Assemble ``max_tokens`` + thinking + sampling + ``output_config``.

    Thinking is applied last on purpose: the legacy ``budget_tokens`` form
    requires a neutral ``temperature``, which must win over a caller-supplied
    one or the API rejects the request.

    Args:
        model: Target model id.
        kwargs: Caller kwargs (``max_tokens``, ``effort``, ``thinking_budget``,
            sampling parameters, ``output_config``).
        streaming: True for a streaming request.

    Returns:
        dict: Request fragment to merge into the ``messages.create`` call.
    """
    request: dict[str, Any] = sampling_kwargs(model, kwargs)
    plan = resolve_thinking(
        effort=kwargs.get("effort"),
        thinking_budget=kwargs.get("thinking_budget"),
        max_tokens=resolve_max_tokens(model, kwargs, streaming=streaming),
    )
    request.update(plan.to_kwargs(model))
    output_config = merge_output_config(
        model, kwargs.get("output_config"), request.get("output_config")
    )
    if output_config:
        request["output_config"] = output_config
    return request


def merge_output_config(
    model: str,
    caller_config: Any,
    resolved_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Combine a caller's ``output_config`` with the one this layer resolved.

    ``output_config`` carries more than the effort tier (structured-output
    formats, and whatever the API adds next), so dropping a caller's copy — as
    the reserved-kwarg filter did — silently discards a request they made.

    A caller-supplied ``effort`` goes through the same clamp as a resolved one:
    a raw tier is exactly the 400 this layer exists to prevent. Where the
    family has no effort surface at all (every budget-thinking family), the key
    is removed rather than degraded — there is no tier that would be accepted.
    The resolved tier still wins on conflict, since it came from this layer.

    Args:
        model: Target model id (for the clamp and the log line).
        caller_config: The caller's ``output_config``, if any.
        resolved_config: What thinking resolution produced, if anything.

    Returns:
        dict: The merged config; empty when neither side supplied one, or when
        the caller's only key was an effort the family cannot take.
    """
    if not isinstance(caller_config, dict) or not caller_config:
        return dict(resolved_config or {})

    caller = dict(caller_config)
    if "effort" in caller:
        raw = caller["effort"]
        tier = clamp_effort(model, raw if isinstance(raw, str) else str(raw))
        if tier is None:
            caller.pop("effort")
            logger.debug(
                "anthropic_output_config_effort_dropped",
                extra={"model": model, "requested": str(raw)},
            )
        else:
            caller["effort"] = tier

    merged = {**caller, **(resolved_config or {})}
    logger.debug(
        # Post-merge, so the line never contradicts a drop logged just above.
        "anthropic_output_config_merged",
        extra={"model": model, "keys": sorted(merged)},
    )
    return merged


def forwardable_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Caller kwargs safe to pass straight through to the SDK."""
    return {k: v for k, v in kwargs.items() if k not in RESERVED_KWARGS}


def resolve_tool_choice(model: str, tool_choice: ToolChoice | None) -> ToolChoice:
    """The tool-choice policy to send, degraded where the family forbids it.

    Fable 5.1 and Mythos 5.1 reject a *forced* choice (``any``/``tool``) with a
    400. Failing the call would be worse than asking: the model can still pick
    the tool on its own, so the policy is relaxed to ``auto`` and the downgrade
    is logged rather than swallowed.

    Args:
        model: Target model id.
        tool_choice: The caller's policy, or ``None`` for the default.

    Returns:
        ToolChoice: The policy to send.
    """
    choice = tool_choice or ToolChoice(mode="auto")
    if (
        choice.mode in ("any", "tool")
        and capabilities_for(model).rejects_forced_tool_choice
    ):
        logger.warning(
            "anthropic_forced_tool_choice_downgraded",
            extra={"model": model, "requested": choice.mode},
        )
        return ToolChoice(mode="auto")
    return choice
