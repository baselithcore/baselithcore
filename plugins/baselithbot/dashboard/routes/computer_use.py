"""Computer Use configuration routes (capability gates + shell allowlist)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request

from ...computer_use import ComputerUseConfig
from ...policies import RateLimiter
from ..bus import _BUS
from ..security import enforce

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def register_computer_use_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
) -> None:
    @router.get("/computer-use")
    async def get_computer_use() -> dict[str, Any]:
        """Return the effective ComputerUse config (boot + runtime overlay)."""
        return {"current": plugin.effective_computer_use_config().model_dump()}

    @router.put("/computer-use", dependencies=[Depends(guard)])
    async def update_computer_use(
        config: ComputerUseConfig, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "computer_use_update")
        updated = plugin.runtime_config.set_computer_use(config)
        await plugin.invalidate_agent()
        _BUS.publish(
            "computer_use.updated",
            {
                "enabled": updated.enabled,
                "allow_mouse": updated.allow_mouse,
                "allow_keyboard": updated.allow_keyboard,
                "allow_screenshot": updated.allow_screenshot,
                "allow_shell": updated.allow_shell,
                "allow_filesystem": updated.allow_filesystem,
                "allowed_shell_commands": len(updated.allowed_shell_commands),
            },
        )
        return {"current": updated.model_dump()}


__all__ = ["register_computer_use_routes"]
