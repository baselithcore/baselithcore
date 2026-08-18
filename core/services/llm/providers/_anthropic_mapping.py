"""Pure request-shaping helpers for the Anthropic provider.

Maps neutral tool/format specs onto Anthropic's wire shapes and decides where
prompt-cache breakpoints are worth spending. Split out of
``anthropic_provider`` to keep that module under the file-size cap; these are
side-effect-free functions with no client dependency, so they test standalone.
"""

from __future__ import annotations

import os
from typing import Any

from core.services.llm.tool_calling import LLMToolSpec, ToolChoice

try:
    import anthropic
except ImportError:  # pragma: no cover - optional dependency
    anthropic = None  # type: ignore

# Prompt caching: the system prompt is the stable prefix (instructions +
# tool/RAG/memory context), re-sent on every call. Marking it with an ephemeral
# cache breakpoint lets Anthropic reuse it (~5 min TTL) instead of re-billing it
# in full — typically a large input-cost and latency win on long prefixes.
_PROMPT_CACHE_ENABLED = os.getenv("BASELITH_LLM_PROMPT_CACHE", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Anthropic silently ignores cache_control on a prefix shorter than the model
# minimum (~1024 tokens Sonnet / 2048 Haiku). Skip obviously-tiny prompts so we
# don't spend a cache breakpoint on something that can never be cached
# (~4 chars/token heuristic → ~1024 tokens).
_PROMPT_CACHE_MIN_CHARS = 4096


def _build_system_param(system_prompt: str) -> Any:
    """Return the Anthropic ``system`` argument, cacheable when worthwhile.

    Emits a single ``text`` block carrying an ephemeral ``cache_control``
    breakpoint when caching is enabled and the prompt is long enough to be
    cacheable; otherwise the plain string (or ``NOT_GIVEN`` when empty).
    """
    if not system_prompt:
        return anthropic.NOT_GIVEN
    if _PROMPT_CACHE_ENABLED and len(system_prompt) >= _PROMPT_CACHE_MIN_CHARS:
        return [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    return system_prompt


def _to_anthropic_tools(tools: list[LLMToolSpec]) -> list[dict[str, Any]]:
    """Map neutral tool specs to Anthropic ``tools`` entries.

    Anthropic's ``input_schema`` is a JSON-Schema object, matching
    :attr:`LLMToolSpec.parameters` directly. ``strict`` is a top-level field on
    the tool definition (not on ``tool_choice``).
    """
    result: list[dict[str, Any]] = []
    for spec in tools:
        entry: dict[str, Any] = {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.parameters or {"type": "object"},
        }
        if spec.strict:
            entry["strict"] = True
        result.append(entry)
    return result


def _apply_tool_cache_control(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the last tool definition with an ephemeral cache breakpoint.

    Tools render *ahead of* ``system`` in Anthropic's prompt, so a breakpoint on
    the final tool caches the whole tool block independently of how long the
    system prompt is — previously the only breakpoint hung off ``system``, so a
    large tool schema was re-billed in full on every call whenever the system
    prompt was short or absent (the common shape in an agentic loop).

    The breakpoint is only spent when the serialized schema is plausibly long
    enough to cache; Anthropic silently ignores a breakpoint on a shorter prefix
    (no error, no charge), but skipping it keeps one of the four available
    breakpoints free for a caller that can use it.
    """
    if not entries or not _PROMPT_CACHE_ENABLED:
        return entries
    serialized = sum(len(str(entry)) for entry in entries)
    if serialized < _PROMPT_CACHE_MIN_CHARS:
        return entries
    marked = list(entries)
    marked[-1] = {**marked[-1], "cache_control": {"type": "ephemeral"}}
    return marked


def _to_anthropic_tool_choice(choice: ToolChoice) -> dict[str, Any]:
    """Map a neutral :class:`ToolChoice` to Anthropic's ``tool_choice`` object."""
    if choice.mode == "tool":
        return {"type": "tool", "name": choice.name}
    # "auto" | "any" | "none" map 1:1 to Anthropic's tool_choice types.
    return {"type": choice.mode}


__all__ = [
    "_PROMPT_CACHE_ENABLED",
    "_PROMPT_CACHE_MIN_CHARS",
    "_apply_tool_cache_control",
    "_build_system_param",
    "_to_anthropic_tool_choice",
    "_to_anthropic_tools",
]
