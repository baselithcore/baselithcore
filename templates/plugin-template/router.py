"""
API Router for MyPlugin.

Optional: Include this if your plugin exposes HTTP endpoints.
Replace 'my-plugin' and 'MyItem' throughout with your plugin's names.
"""

from fastapi import APIRouter

from .models import MyItem

router = APIRouter(prefix="/my-plugin", tags=["my-plugin"])


@router.get("/health")
async def health_check():
    """Plugin health check endpoint."""
    return {"status": "ok", "plugin": "my-plugin"}


@router.get("/items", response_model=list[MyItem])
async def list_items():
    """List all items."""
    # TODO: Implement
    return []


@router.post("/items", response_model=MyItem)
async def create_item(item: MyItem):
    """Create new item."""
    # TODO: Implement
    return item
