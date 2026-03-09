"""Example Plugin Router module."""

from typing import Dict, Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel


class ExampleResponse(BaseModel):
    """Response model for example endpoint."""

    message: str
    plugin_name: str
    version: str


def create_router(plugin_instance: Any) -> APIRouter:
    """
    Create and configure the API router.

    Args:
        plugin_instance: The instance of the plugin (to access metadata/config)

    Returns:
        Configured APIRouter
    """
    from core.middleware import require_user

    router = APIRouter(
        prefix="",
        tags=["example"],
        dependencies=[Depends(require_user)],
    )

    @router.get("/hello", response_model=ExampleResponse)
    async def hello():
        """Example endpoint that returns a greeting."""
        return ExampleResponse(
            message="Hello from example plugin!",
            plugin_name=plugin_instance.metadata.name,
            version=plugin_instance.metadata.version,
        )

    @router.get("/config")
    async def get_config() -> Dict[str, Any]:
        """Get plugin configuration."""
        return {
            "plugin": plugin_instance.metadata.name,
            "config": plugin_instance.get_config("example_key", "default_value"),
        }

    @router.post("/echo")
    async def echo(message: str) -> Dict[str, str]:
        """Echo a message."""
        return {"echo": message, "plugin": plugin_instance.metadata.name}

    return router
