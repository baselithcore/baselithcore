"""Handlers Mixin for Orchestrator."""

from core.observability.logging import get_logger
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.plugins import PluginRegistry
    from core.orchestration.protocols import FlowHandler, StreamHandler

logger = get_logger(__name__)


class HandlersMixin:
    """Mixin for flow and stream handlers registry."""

    plugin_registry: Optional["PluginRegistry"]
    _flow_handlers: Dict[str, "FlowHandler"]
    _stream_handlers: Dict[str, "StreamHandler"]

    def _load_plugin_handlers(self) -> None:
        """Load flow handlers from plugin registry."""
        if not self.plugin_registry:
            return

        try:
            plugin_handlers = self.plugin_registry.get_all_flow_handlers()
            for intent, handler in plugin_handlers.items():
                self.register_handler(intent, handler)
                logger.info(f"Loaded plugin handler for intent: {intent}")
        except Exception as e:
            logger.warning(f"Failed to load plugin handlers: {e}")

    def register_handler(
        self,
        intent: str,
        handler: "FlowHandler",
        stream_handler: Optional["StreamHandler"] = None,
    ) -> None:
        """
        Register a flow handler for an intent.

        Args:
            intent: Intent name to handle
            handler: Flow handler instance
            stream_handler: Optional streaming handler for the same intent
        """
        self._flow_handlers[intent] = handler
        if stream_handler:
            self._stream_handlers[intent] = stream_handler
        logger.debug(f"Registered handler for intent: {intent}")

    def get_registered_intents(self) -> list[str]:
        """Get list of registered intent names."""
        return list(self._flow_handlers.keys())

    def has_stream_handler(self, intent: str) -> bool:
        """Check if a streaming handler exists for an intent."""
        return intent in self._stream_handlers
