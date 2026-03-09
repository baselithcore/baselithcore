"""Router plugin interface for plugins that provide API routes."""

from typing import Any, List
from abc import abstractmethod

from .interface import Plugin


class RouterPlugin(Plugin):
    """
    Plugin that provides FastAPI routers.

    Router plugins can add custom API endpoints to the application.
    """

    @abstractmethod
    def create_router(self) -> Any:
        """
        Factory method to create a FastAPI router.

        Returns:
            APIRouter instance with plugin routes
        """
        pass

    def get_routers(self) -> List[Any]:
        """
        Return list of routers provided by this plugin.

        By default, returns a single router created by create_router.
        Override this method if the plugin provides multiple routers.

        Returns:
            List of APIRouter instances
        """
        return [self.create_router()]

    def get_router_prefix(self) -> str:
        """
        Get the URL prefix for this plugin's routes.

        Returns:
            URL prefix (e.g., "/api/myplugin")
        """
        return f"/api/{self.metadata.name}"

    def get_router_tags(self) -> List[str]:
        """
        Get OpenAPI tags for this plugin's routes.

        Returns:
            List of tags for API documentation
        """
        return [self.metadata.name]

    def get_router_config(self) -> dict:
        """
        Get router-specific configuration.

        Returns:
            Dictionary of router configuration
        """
        return self.get_config("router", {})
