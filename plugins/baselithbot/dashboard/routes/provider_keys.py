"""Provider API-key storage/test routes (encrypted at rest)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...policies import RateLimiter
from ...secret_store import ALLOWED_PROVIDERS, SecretStoreError
from ..bus import _BUS
from ..schemas import ProviderKeyRequest
from ..security import enforce, probe_provider

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def register_provider_keys_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
    delete_rate_limit: RateLimiter,
) -> None:
    @router.get("/provider-keys")
    async def list_provider_keys() -> dict[str, Any]:
        """Return the configured-status snapshot (no plaintext ever)."""
        return {
            "providers": plugin.secret_store.snapshot(),
            "allowed": sorted(ALLOWED_PROVIDERS),
        }

    @router.put("/provider-keys/{provider}", dependencies=[Depends(guard)])
    async def set_provider_key(
        provider: str,
        body: ProviderKeyRequest,
        request: Request,
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "provider_keys_set")
        try:
            entry = plugin.secret_store.set(provider, body.api_key)
        except SecretStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _BUS.publish(
            "provider_keys.updated",
            {"provider": entry["provider"], "last4": entry["last4"]},
        )
        return entry

    @router.delete("/provider-keys/{provider}", dependencies=[Depends(guard)])
    async def delete_provider_key(provider: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "provider_keys_delete")
        try:
            removed = plugin.secret_store.delete(provider)
        except SecretStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if removed:
            _BUS.publish(
                "provider_keys.deleted",
                {"provider": provider.strip().lower()},
            )
        return {"provider": provider.strip().lower(), "removed": removed}

    @router.post("/provider-keys/{provider}/test", dependencies=[Depends(guard)])
    async def test_provider_key(provider: str, request: Request) -> dict[str, Any]:
        """Validate the stored key by issuing a minimal provider call."""
        enforce(token_rate_limit, request, "provider_keys_test")
        try:
            key = plugin.secret_store.get_plaintext(provider)
        except SecretStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if key is None:
            raise HTTPException(
                status_code=404, detail="no key configured for provider"
            )
        ok, detail = await probe_provider(provider.strip().lower(), key)
        return {"provider": provider.strip().lower(), "ok": ok, "detail": detail}


__all__ = ["register_provider_keys_routes"]
