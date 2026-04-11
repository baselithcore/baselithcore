"""Official API Routers plugin."""

from core.plugins import Plugin


class ApiRoutersPlugin(Plugin):
    """Plugin packaging the framework's default HTTP routers."""

    async def initialize(self, config: dict) -> None:
        await super().initialize(config)
