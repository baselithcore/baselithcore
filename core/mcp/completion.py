"""``completion/complete``: argument autocompletion for prompts and templates.

A registered provider is either a static list of candidates (filtered here by
the partial value the user has typed) or a callable that does its own lookup —
the callable form is what a provider backed by a database or an API needs.
"""

from __future__ import annotations

import inspect
from typing import Any

from core.mcp.errors import InvalidParams
from core.observability.logging import get_logger

logger = get_logger(__name__)

# The spec bounds a single completion response to 100 values.
MAX_COMPLETION_VALUES = 100


async def _resolve_candidates(provider: Any, partial: str) -> list[str]:
    """Run *provider* and normalize its output to a list of strings."""
    if callable(provider):
        result = provider(partial)
        if inspect.isawaitable(result):
            result = await result
        return [str(value) for value in result]
    # Static list: filter by what has been typed so far.
    return [str(value) for value in provider if str(value).startswith(partial)]


class CompletionHandlerMixin:
    """Mixin serving ``completion/complete`` for prompts and resource templates."""

    _prompts: dict[str, Any]
    _resource_templates: dict[str, Any]

    def _has_completions(self) -> bool:
        """True when any registered primitive declares completion providers."""
        return any(
            getattr(target, "completions", None)
            for registry in (self._prompts, self._resource_templates)
            for target in registry.values()
        )

    def _completion_providers(self, ref: dict[str, Any]) -> dict[str, Any]:
        """Find the completion providers declared by the referenced primitive.

        Raises:
            InvalidParams: The reference names nothing this server serves.
        """
        ref_type = ref.get("type")
        if ref_type == "ref/prompt":
            target = self._prompts.get(ref.get("name", ""))
        elif ref_type == "ref/resource":
            target = self._resource_templates.get(ref.get("uri", ""))
        else:
            raise InvalidParams(f"Unsupported completion reference type: {ref_type}")

        if target is None:
            raise InvalidParams(f"Unknown completion reference: {ref}")
        return getattr(target, "completions", None) or {}

    async def _handle_complete(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle completion/complete."""
        ref = params.get("ref") or {}
        argument = params.get("argument") or {}
        providers = self._completion_providers(ref)

        provider = providers.get(argument.get("name", ""))
        if provider is None:
            # No suggestions for this argument is a normal answer, not an error.
            return {"completion": {"values": [], "total": 0, "hasMore": False}}

        try:
            candidates = await _resolve_candidates(provider, argument.get("value", ""))
        except Exception as exc:
            # Autocompletion is advisory: a broken provider must not fail the
            # request the user is in the middle of typing.
            logger.warning("mcp_completion_provider_failed", error=str(exc))
            return {"completion": {"values": [], "total": 0, "hasMore": False}}

        total = len(candidates)
        return {
            "completion": {
                "values": candidates[:MAX_COMPLETION_VALUES],
                "total": total,
                "hasMore": total > MAX_COMPLETION_VALUES,
            }
        }


__all__ = ["MAX_COMPLETION_VALUES", "CompletionHandlerMixin"]
