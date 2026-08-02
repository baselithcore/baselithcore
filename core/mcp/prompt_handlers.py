"""``prompts/list`` and ``prompts/get`` handlers.

Prompts are the user-facing MCP primitive: templated messages a host surfaces
as slash commands or menu entries, rendered server-side from named arguments.
"""

from __future__ import annotations

from typing import Any

from core.mcp.errors import InvalidParams
from core.mcp.pagination import page_registry, with_cursor


def _prompt_entry(prompt: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": prompt.name,
        "description": prompt.description,
        "arguments": prompt.arguments,
    }
    if prompt.icons:
        entry["icons"] = prompt.icons
    return entry


def _as_messages(rendered: Any) -> list[dict[str, Any]]:
    """Normalize a handler's return value into PromptMessage objects."""
    if isinstance(rendered, str):
        return [{"role": "user", "content": {"type": "text", "text": rendered}}]
    if isinstance(rendered, list):
        return rendered
    raise TypeError(
        f"Prompt handler must return str or list[PromptMessage], got "
        f"{type(rendered).__name__}"
    )


class PromptHandlerMixin:
    """Mixin serving the prompts primitive."""

    _prompts: dict[str, Any]
    config: Any

    async def _handle_list_prompts(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle prompts/list (paginated)."""
        page, next_cursor = page_registry(self._prompts, params, self.config)
        return with_cursor(
            {"prompts": [_prompt_entry(prompt) for prompt in page]}, next_cursor
        )

    async def _handle_get_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle prompts/get: render one prompt with the given arguments."""
        name = params.get("name", "")
        arguments = params.get("arguments") or {}

        prompt = self._prompts.get(name)
        if prompt is None or prompt.handler is None:
            raise InvalidParams(f"Unknown prompt: {name}")
        if not isinstance(arguments, dict):
            raise InvalidParams(f"Invalid arguments for prompt {name}: expected object")

        missing = [
            argument["name"]
            for argument in prompt.arguments
            if argument.get("required") and argument["name"] not in arguments
        ]
        if missing:
            raise InvalidParams(
                f"Missing required argument(s) for prompt {name}: {', '.join(missing)}"
            )

        rendered = await prompt.handler(**arguments)
        return {"description": prompt.description, "messages": _as_messages(rendered)}


__all__ = ["PromptHandlerMixin"]
