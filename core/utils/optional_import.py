"""Small helpers for optional compatibility imports."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any

from fastapi import APIRouter, HTTPException


def optional_module(module_name: str) -> ModuleType | None:
    """Import an optional module, returning ``None`` only when it is absent."""
    try:
        return import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name or module_name.startswith(f"{exc.name}."):
            return None
        raise


def optional_router(module_name: str) -> tuple[ModuleType | None, APIRouter]:
    module = optional_module(module_name)
    if module is not None:
        router = getattr(module, "router", None)
        if router is not None:
            return module, router
    return None, APIRouter()


async def unavailable_admin_credentials(*_args: Any, **_kwargs: Any) -> str:
    raise HTTPException(status_code=404, detail="Admin router plugin is not installed")


__all__ = ["optional_module", "optional_router", "unavailable_admin_credentials"]
