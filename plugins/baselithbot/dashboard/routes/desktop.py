"""Desktop (Computer Use) tool invocation routes.

Exposes the Computer Use MCP tool surface as direct HTTP endpoints so the
dashboard can drive single-tool actions (screenshot, shell, mouse, kbd,
fs) without going through the full MCP client. Every call re-resolves the
current ``ComputerUseConfig`` so runtime overlay edits take effect
immediately after save.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...policies import RateLimiter
from ..bus import _BUS
from ..security import enforce

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


class DesktopToolInvokeRequest(BaseModel):
    """Generic tool invocation payload — arguments forwarded to the handler."""

    args: dict[str, Any] = Field(default_factory=dict)


def register_desktop_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
) -> None:
    @router.get("/desktop/tools")
    async def list_desktop_tools() -> dict[str, Any]:
        """Return the current Computer Use tool surface + policy snapshot.

        Read-only — no ``guard`` dependency so the UI can render capability
        state before authenticating a write.
        """
        tools = plugin.build_computer_tool_map()
        cu = plugin.effective_computer_use_config()
        return {
            "policy": {
                "enabled": cu.enabled,
                "allow_mouse": cu.allow_mouse,
                "allow_keyboard": cu.allow_keyboard,
                "allow_screenshot": cu.allow_screenshot,
                "allow_shell": cu.allow_shell,
                "allow_filesystem": cu.allow_filesystem,
                "allowed_shell_commands": list(cu.allowed_shell_commands),
                "filesystem_root": cu.filesystem_root,
                "filesystem_max_bytes": cu.filesystem_max_bytes,
                "shell_timeout_seconds": cu.shell_timeout_seconds,
                "audit_log_path": cu.audit_log_path,
            },
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["input_schema"],
                }
                for t in tools.values()
            ],
        }

    @router.post("/desktop/tools/{tool_name}", dependencies=[Depends(guard)])
    async def invoke_desktop_tool(
        tool_name: str, payload: DesktopToolInvokeRequest, request: Request
    ) -> dict[str, Any]:
        """Invoke a single Computer Use tool by name.

        Returns the tool's own ``{"status": "success"|"denied"|"error"}``
        envelope verbatim — capability gating stays enforced inside the
        handler, so the API layer only rate-limits and logs.
        """
        enforce(token_rate_limit, request, f"desktop_tool:{tool_name}")
        tools = plugin.build_computer_tool_map()
        spec = tools.get(tool_name)
        if spec is None:
            raise HTTPException(
                status_code=404, detail=f"unknown computer-use tool: {tool_name}"
            )
        handler = spec["handler"]
        try:
            result = await handler(**payload.args)
        except TypeError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid arguments: {exc}"
            ) from exc
        _BUS.publish(
            "desktop.tool_invoked",
            {
                "tool": tool_name,
                "status": result.get("status") if isinstance(result, dict) else None,
            },
        )
        return {"tool": tool_name, "result": result}


__all__ = ["register_desktop_routes"]
