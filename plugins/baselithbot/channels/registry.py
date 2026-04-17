"""Registry of channel adapters keyed by channel name."""

from __future__ import annotations

from typing import Any, Callable

from core.observability.logging import get_logger

from .base import ChannelAdapter, ChannelMessage

logger = get_logger(__name__)

AdapterFactory = Callable[[dict[str, Any]], ChannelAdapter]


class ChannelRegistry:
    """Holds adapter factories and instantiates configured channels."""

    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}
        self._instances: dict[str, ChannelAdapter] = {}

    def register(self, name: str, factory: AdapterFactory) -> None:
        if name in self._factories:
            logger.warning("baselithbot_channel_replaced", channel=name)
        self._factories[name] = factory

    def known(self) -> list[str]:
        return sorted(self._factories.keys())

    async def get_or_create(
        self, name: str, config: dict[str, Any] | None = None
    ) -> ChannelAdapter:
        if name not in self._factories:
            raise KeyError(f"channel '{name}' is not registered")
        if name not in self._instances:
            adapter = self._factories[name](config or {})
            await adapter.startup()
            self._instances[name] = adapter
        return self._instances[name]

    async def send(
        self, name: str, message: ChannelMessage, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        adapter = await self.get_or_create(name, config)
        return await adapter.send(message)

    async def shutdown_all(self) -> None:
        for adapter in self._instances.values():
            try:
                await adapter.shutdown()
            except Exception as exc:
                logger.warning(
                    "baselithbot_channel_shutdown_error",
                    channel=adapter.name,
                    error=str(exc),
                )
        self._instances.clear()


__all__ = ["ChannelRegistry", "AdapterFactory"]
