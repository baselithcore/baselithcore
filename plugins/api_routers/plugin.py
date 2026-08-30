"""Official API Routers plugin."""

from typing import Any

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
        self._register_privacy_providers()

    @staticmethod
    def _register_privacy_providers() -> None:
        """Register core DSR data providers when privacy + Postgres are enabled.

        Done here (the plugin lifecycle hook) rather than in ``core`` so the
        framework keeps no ``core -> plugins`` edge. Registration is idempotent —
        the registry replaces a provider by name — so re-init is safe.
        """
        from core.config.privacy import get_privacy_config
        from core.config.storage import get_storage_config

        if not get_privacy_config().enabled:
            return
        if not get_storage_config().postgres_enabled:
            return

        from core.privacy import PostgresDataProvider, register_data_provider

        register_data_provider(PostgresDataProvider())

    def get_router_prefix(self) -> str:
        # Mount the exposed routers at their own declared prefixes (e.g.
        # ``/webhooks``) rather than under ``/api/{plugin}``.
        return ""

    def get_routers(self) -> list[Any]:
        """Expose opt-in routers. Empty unless their feature flag is enabled."""
        routers: list[Any] = []

        # Prompt-catalog admin surface: reads are always useful (local
        # registry); writes self-gate on the synchronizer (503 without
        # BASELITH_PROMPT_SYNC), so the router mounts unconditionally.
        from plugins.api_routers.prompts import router as prompts_router

        routers.append(prompts_router)

        # WebSocket chat channel: authenticates at the handshake with the
        # same credentials as the REST chat surface.
        from plugins.api_routers.chat_ws import router as chat_ws_router

        routers.append(chat_ws_router)

        from core.config.webhooks import get_webhook_config

        if get_webhook_config().enabled:
            from plugins.api_routers.webhooks import router as webhooks_router

            routers.append(webhooks_router)

        from core.config.privacy import get_privacy_config

        if get_privacy_config().enabled:
            from plugins.api_routers.privacy import router as privacy_router

            routers.append(privacy_router)

        from core.config.compliance import get_compliance_config

        if get_compliance_config().enabled:
            from plugins.api_routers.compliance import router as compliance_router

            routers.append(compliance_router)

        from core.config.orchestration import get_orchestration_config

        if get_orchestration_config().checkpoint_enabled:
            from plugins.api_routers.approvals import router as approvals_router
            from plugins.api_routers.runs import router as runs_router

            routers.append(approvals_router)
            routers.append(runs_router)
        return routers
