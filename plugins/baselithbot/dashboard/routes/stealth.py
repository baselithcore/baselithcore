"""Stealth configuration routes (UA rotation, webdriver mask, lang/tz spoof)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request

from ...policies import RateLimiter
from ...types import StealthConfig
from ..bus import _BUS
from ..security import enforce

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def register_stealth_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
) -> None:
    @router.get("/stealth")
    async def get_stealth() -> dict[str, Any]:
        """Return the effective Stealth config (boot + runtime overlay)."""
        return {"current": plugin.effective_stealth_config().model_dump()}

    @router.put("/stealth", dependencies=[Depends(guard)])
    async def update_stealth(
        config: StealthConfig, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "stealth_update")
        updated = plugin.runtime_config.set_stealth(config)
        await plugin.invalidate_agent()
        _BUS.publish(
            "stealth.updated",
            {
                "enabled": updated.enabled,
                "rotate_user_agent": updated.rotate_user_agent,
                "mask_webdriver": updated.mask_webdriver,
                "spoof_languages": list(updated.spoof_languages),
                "spoof_timezone": updated.spoof_timezone,
                "user_agents": len(updated.user_agents),
            },
        )
        return {"current": updated.model_dump()}


__all__ = ["register_stealth_routes"]
