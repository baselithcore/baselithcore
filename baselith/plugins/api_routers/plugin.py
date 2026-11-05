"""Official API Routers plugin."""

from typing import Any

from baselith.core.plugins import Plugin


class ApiRoutersPlugin(Plugin):
    """Plugin packaging the framework's default HTTP routers.

    The core data/admin routers (chat, feedback, tenant, …) are mounted by the
    app factory. Newer, opt-in routers are exposed here via the RouterPlugin
    mounting path (``get_routers``) so they attach at startup without the app
    factory taking a ``core -> plugins`` import: prompts, the WebSocket chat
    channel, the async-agent API, and — behind their feature flags — webhooks,
    privacy, compliance, approvals, runs, the workflow catalog, the knowledge API and the
    online-evaluation summary.
    Every API router is served under ``/v1`` and, deprecated, at its
    unprefixed path (:mod:`baselith.core.api.versioning`).
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
        from baselith.core.config.privacy import get_privacy_config
        from baselith.core.config.storage import get_storage_config

        if not get_privacy_config().enabled:
            return
        if not get_storage_config().postgres_enabled:
            return

        from baselith.core.privacy import PostgresDataProvider, register_data_provider

        register_data_provider(PostgresDataProvider())

    def get_router_prefix(self) -> str:
        # Mount the exposed routers at their own declared prefixes (e.g.
        # ``/webhooks``) rather than under ``/api/{plugin}``.
        return ""

    def get_routers(self) -> list[Any]:
        """Expose the plugin's routers, the API ones versioned.

        Every API router is returned twice (see :mod:`baselith.core.api.versioning`):
        under ``/v1`` and, marked deprecated, at its historical unprefixed
        path. The WebSocket channel is served at ``/v1/chat/ws`` (canonical)
        and at ``/chat/ws`` (deprecated in the docs; a handshake carries no
        ``Deprecation`` header).
        """
        from baselith.core.api.versioning import (
            versioned_routers,
            versioned_websocket_routers,
        )

        # WebSocket chat channel: authenticates at the handshake with the
        # same credentials as the REST chat surface.
        from baselith.plugins.api_routers.routes.chat_ws import router as chat_ws_router

        routers: list[Any] = []

        # Prompt-catalog admin surface: reads are always useful (local
        # registry); writes self-gate on the synchronizer (503 without
        # BASELITH_PROMPT_SYNC), so the router mounts unconditionally.
        from baselith.plugins.api_routers.routes.prompts import router as prompts_router

        routers.append(prompts_router)

        from baselith.core.config.webhooks import get_webhook_config

        if get_webhook_config().enabled:
            from baselith.plugins.api_routers.routes.webhooks import (
                router as webhooks_router,
            )

            routers.append(webhooks_router)

        from baselith.core.config.privacy import get_privacy_config

        if get_privacy_config().enabled:
            from baselith.plugins.api_routers.routes.privacy import (
                router as privacy_router,
            )

            routers.append(privacy_router)

        from baselith.core.config.compliance import get_compliance_config

        if get_compliance_config().enabled:
            from baselith.plugins.api_routers.compliance import (
                router as compliance_router,
            )

            routers.append(compliance_router)

        from baselith.core.config.orchestration import get_orchestration_config

        if get_orchestration_config().checkpoint_enabled:
            from baselith.plugins.api_routers.routes.approvals import (
                router as approvals_router,
            )
            from baselith.plugins.api_routers.routes.runs import router as runs_router

            routers.append(approvals_router)
            routers.append(runs_router)

        from baselith.core.config.knowledge import get_knowledge_config

        if get_knowledge_config().enabled:
            from baselith.plugins.api_routers.routes.knowledge import (
                router as knowledge_router,
            )

            routers.append(knowledge_router)

        from baselith.core.config.workflows import get_workflows_config

        if get_workflows_config().enabled:
            from baselith.plugins.api_routers.routes.workflows import (
                router as workflows_router,
            )
            from baselith.plugins.api_routers.routes.workflows import (
                trigger_router as workflows_trigger_router,
            )

            routers.append(workflows_router)
            routers.append(workflows_trigger_router)

        from baselith.core.config.evaluation import get_evaluation_config

        if get_evaluation_config().online_enabled:
            from baselith.plugins.api_routers.routes.evaluation import (
                router as evaluation_router,
            )

            routers.append(evaluation_router)

        # Async agent runs (POST /agent/async + GET /agent/status/{id}).
        # Mounted unconditionally: queue-infrastructure absence surfaces as
        # 503 at call time, not as a missing route.
        from baselith.plugins.api_routers.routes.async_runs import (
            router as async_runs_router,
        )

        routers.append(async_runs_router)
        return [
            *versioned_websocket_routers([chat_ws_router]),
            *versioned_routers(routers),
        ]
