"""Official API Routers plugin."""

from typing import Any, List

from core.plugins import Plugin


class ApiRoutersPlugin(Plugin):
    """Plugin packaging the framework's default HTTP routers.

    The core data/admin routers (chat, feedback, tenant, …) are mounted by the
    app factory. Newer, opt-in routers are exposed here via the RouterPlugin
    mounting path (``get_routers``) so they attach at startup without the app
    factory taking a ``core -> plugins`` import. Currently this mounts the
    webhook management API when ``WEBHOOKS_ENABLED`` is set.
    """

    async def initialize(self, config: dict) -> None:
        await super().initialize(config)

    def get_router_prefix(self) -> str:
        # Mount the exposed routers at their own declared prefixes (e.g.
        # ``/webhooks``) rather than under ``/api/{plugin}``.
        return ""

    def get_routers(self) -> List[Any]:
        """Expose opt-in routers. Empty unless their feature flag is enabled."""
        routers: List[Any] = []
        from core.config.webhooks import get_webhook_config

        if get_webhook_config().enabled:
            from plugins.api_routers.webhooks import router as webhooks_router

            routers.append(webhooks_router)
        return routers
