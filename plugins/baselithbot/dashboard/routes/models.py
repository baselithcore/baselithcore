"""Model preferences routes (LLM + vision provider selection)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request

from core.config import get_vision_config

from plugins.baselithbot.config.models import (
    KNOWN_PROVIDERS,
    KNOWN_VISION_PROVIDERS,
    ModelPreferences,
)
from plugins.baselithbot.diagnostics.ollama_probe import fetch_ollama_catalog
from plugins.baselithbot.policies import RateLimiter
from plugins.baselithbot.dashboard.bus import _BUS
from plugins.baselithbot.dashboard.security import enforce

if TYPE_CHECKING:
    from plugins.baselithbot.plugin import BaselithbotPlugin


def register_models_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
) -> None:
    @router.get("/models")
    async def get_models() -> dict[str, Any]:
        """Expose operator-selected model prefs + catalog of known options.

        Ollama options come from a live probe of ``/api/tags`` so the picker
        reflects actually-installed local models rather than a hardcoded list.
        Probe failures silently fall back to the static catalog.
        """
        vision_cfg = get_vision_config()
        tags = await fetch_ollama_catalog(vision_cfg.ollama_url)

        llm_providers = {k: list(v) for k, v in KNOWN_PROVIDERS.items()}
        vision_providers = {k: list(v) for k, v in KNOWN_VISION_PROVIDERS.items()}
        if tags["llm"]:
            llm_providers["ollama"] = tags["llm"]
        if tags["vision"]:
            vision_providers["ollama"] = tags["vision"]

        return {
            "current": plugin.model_preferences.get().model_dump(),
            "options": {
                "llm_providers": llm_providers,
                "vision_providers": vision_providers,
            },
        }

    @router.put("/models", dependencies=[Depends(guard)])
    async def update_models(
        prefs: ModelPreferences, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "models_update")
        updated = plugin.model_preferences.update(prefs)
        plugin._apply_model_preferences()
        await plugin.invalidate_agent()
        _BUS.publish(
            "models.updated",
            {
                "provider": updated.provider,
                "model": updated.model,
                "vision_provider": updated.vision_provider,
                "vision_model": updated.vision_model,
                "failover_entries": len(updated.failover_chain),
            },
        )
        return {"current": updated.model_dump()}


__all__ = ["register_models_routes"]
