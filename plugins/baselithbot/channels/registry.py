"""Registry of channel adapters keyed by channel name."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.observability.logging import get_logger
from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage

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

    def is_live(self, name: str) -> bool:
        return name in self._instances

    def live_names(self) -> list[str]:
        return sorted(self._instances.keys())

    def factory_for(self, name: str) -> AdapterFactory:
        if name not in self._factories:
            raise KeyError(f"channel '{name}' is not registered")
        return self._factories[name]

    def required_credentials(self, name: str) -> tuple[str, ...]:
        """Return the declared ``requires_credentials`` tuple for a channel.

        Instantiates a throwaway adapter with an empty config purely to read
        the class-level attribute, since ``requires_credentials`` is declared
        on the adapter subclass (not on the factory).
        """
        factory = self.factory_for(name)
        probe = factory({})
        return tuple(getattr(probe, "requires_credentials", ()))

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

    async def start(self, name: str, config: dict[str, Any] | None = None) -> ChannelAdapter:
        """Force-start (re-instantiate) an adapter with fresh config."""
        if name in self._instances:
            await self.stop(name)
        return await self.get_or_create(name, config)

    async def stop(self, name: str) -> bool:
        adapter = self._instances.pop(name, None)
        if adapter is None:
            return False
        try:
            await adapter.shutdown()
        except Exception as exc:
            logger.warning(
                "baselithbot_channel_stop_error",
                channel=name,
                error=str(exc),
            )
        return True

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
