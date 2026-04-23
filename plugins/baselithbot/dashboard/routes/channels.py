"""Channel lifecycle + config routes (encrypted at rest, masked on read)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from plugins.baselithbot.channels.base import ChannelMessage
from plugins.baselithbot.policies import RateLimiter
from plugins.baselithbot.security.secret_store import SecretStoreError
from plugins.baselithbot.dashboard.bus import _BUS
from plugins.baselithbot.dashboard.schemas import ChannelConfigRequest, ChannelTestRequest
from plugins.baselithbot.dashboard.security import enforce

if TYPE_CHECKING:
    from plugins.baselithbot.plugin import BaselithbotPlugin


def register_channels_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
    delete_rate_limit: RateLimiter,
) -> None:
    def _require_known(name: str) -> tuple[str, ...]:
        try:
            return plugin.channels.required_credentials(name)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"unknown channel '{name}'"
            ) from exc

    @router.get("/channels/{name}/config")
    async def get_channel_config(name: str) -> dict[str, Any]:
        required = _require_known(name)
        snap = plugin.channel_configs.snapshot_entry(name, required)
        return {
            "name": name,
            "required_fields": snap["required_fields"],
            "missing_fields": snap["missing_fields"],
            "configured": snap["configured"],
            "enabled": snap["enabled"],
            "live": plugin.channels.is_live(name),
            "safe_config": snap["safe_config"],
            "updated_at": snap["updated_at"],
        }

    @router.put("/channels/{name}/config", dependencies=[Depends(guard)])
    async def set_channel_config(
        name: str, body: ChannelConfigRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "channel_config_set")
        _require_known(name)
        try:
            merged = dict(plugin.channel_configs.get_config(name) or {})
            merged.update(body.config)
            for field in body.unset_fields:
                merged.pop(field, None)
            plugin.channel_configs.set(name, merged)
        except SecretStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _BUS.publish("channel.config_updated", {"channel": name})
        return {"status": "saved", "channel": name}

    @router.delete("/channels/{name}/config", dependencies=[Depends(guard)])
    async def delete_channel_config(name: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "channel_config_delete")
        _require_known(name)
        if plugin.channels.is_live(name):
            await plugin.channels.stop(name)
        removed = plugin.channel_configs.delete(name)
        if removed:
            _BUS.publish("channel.config_deleted", {"channel": name})
        return {"status": "deleted" if removed else "noop", "channel": name}

    @router.post("/channels/{name}/start", dependencies=[Depends(guard)])
    async def start_channel(name: str, request: Request) -> dict[str, Any]:
        enforce(token_rate_limit, request, "channel_start")
        required = _require_known(name)
        snap = plugin.channel_configs.snapshot_entry(name, required)
        if snap["missing_fields"]:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "missing_required_fields",
                    "missing": snap["missing_fields"],
                },
            )
        cfg = plugin.channel_configs.get_config(name) or {}
        try:
            adapter = await plugin.channels.start(name, cfg)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"channel start failed: {exc}"
            ) from exc
        try:
            plugin.channel_configs.set_enabled(name, True)
        except SecretStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _BUS.publish(
            "channel.started",
            {"channel": name, "status": adapter.status.value},
        )
        return {
            "status": "started",
            "channel": name,
            "adapter_status": adapter.status.value,
        }

    @router.post("/channels/{name}/stop", dependencies=[Depends(guard)])
    async def stop_channel(name: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "channel_stop")
        _require_known(name)
        stopped = await plugin.channels.stop(name)
        if plugin.channel_configs.has(name):
            try:
                plugin.channel_configs.set_enabled(name, False)
            except SecretStoreError:
                pass
        if stopped:
            _BUS.publish("channel.stopped", {"channel": name})
        return {"status": "stopped" if stopped else "noop", "channel": name}

    @router.post("/channels/{name}/test", dependencies=[Depends(guard)])
    async def test_channel(
        name: str, body: ChannelTestRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "channel_test")
        _require_known(name)
        cfg = plugin.channel_configs.get_config(name) or {}
        message = ChannelMessage(
            channel=name,
            target=body.target,
            text=body.text,
            metadata={"test": True},
        )
        try:
            result = await plugin.channels.send(name, message, cfg)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"channel test failed: {exc}"
            ) from exc
        return {"channel": name, "result": result}


__all__ = ["register_channels_routes"]
