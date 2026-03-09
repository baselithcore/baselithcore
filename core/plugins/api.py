"""FastAPI endpoints for plugin hot-reload management."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from .hotreload import HotReloadController
from .lifecycle import PluginState
from .metrics import get_metrics_collector

router = APIRouter(prefix="/api/plugins", tags=["Plugin Management"])


class PluginEnableRequest(BaseModel):
    """Request to enable a plugin."""

    config: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional plugin configuration"
    )


class PluginReloadRequest(BaseModel):
    """Request to reload a plugin."""

    config: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional new configuration"
    )


class PluginActionResponse(BaseModel):
    """Response for plugin actions."""

    success: bool
    message: str
    plugin_name: str
    state: Optional[str] = None


class PluginListResponse(BaseModel):
    """Response for plugin listing."""

    plugins: List[Dict[str, Any]]
    total: int
    active: int
    disabled: int
    failed: int


class PluginStatusResponse(BaseModel):
    """Response for plugin status."""

    lifecycle: Dict[str, Any]
    dependency_graph: Dict[str, List[str]]


# Global controller instance (will be set during app startup)
_controller: Optional[HotReloadController] = None


def set_hot_reload_controller(controller: HotReloadController) -> None:
    """
    Set the global hot-reload controller instance.

    This should be called during application startup.
    """
    global _controller
    _controller = controller


def get_controller() -> HotReloadController:
    """Get hot-reload controller or raise error."""
    if _controller is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin hot-reload system not initialized",
        )
    return _controller


@router.get("/", response_model=PluginListResponse)
async def list_plugins():
    """
    List all plugins with their current state.

    Returns:
        List of plugins with metadata and state information
    """
    controller = get_controller()
    lifecycle = controller.lifecycle

    plugins_data = []
    states = lifecycle.get_all_states()

    for plugin_name, state in states.items():
        plugin = controller.registry.get(plugin_name)
        metadata = lifecycle.get_plugin_metadata(plugin_name) or {}

        plugin_info = {
            "name": plugin_name,
            "state": state.value,
            "version": plugin.metadata.version if plugin else None,
            "description": plugin.metadata.description if plugin else None,
            "author": plugin.metadata.author if plugin else None,
            "dependencies": plugin.metadata.plugin_dependencies if plugin else {},
            "required_resources": plugin.metadata.required_resources if plugin else [],
            "metadata": metadata,
        }
        plugins_data.append(plugin_info)

    # Count by state
    active_count = sum(1 for s in states.values() if s == PluginState.ACTIVE)
    disabled_count = sum(1 for s in states.values() if s == PluginState.DISABLED)
    failed_count = sum(1 for s in states.values() if s == PluginState.FAILED)

    return PluginListResponse(
        plugins=plugins_data,
        total=len(plugins_data),
        active=active_count,
        disabled=disabled_count,
        failed=failed_count,
    )


@router.get("/{plugin_name}", response_model=Dict[str, Any])
async def get_plugin_info(plugin_name: str):
    """
    Get detailed information about a specific plugin.

    Args:
        plugin_name: Name of the plugin

    Returns:
        Plugin details including state, metadata, and dependencies
    """
    controller = get_controller()
    lifecycle = controller.lifecycle

    state = lifecycle.get_state(plugin_name)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plugin '{plugin_name}' not found",
        )

    plugin = controller.registry.get(plugin_name)
    metadata = lifecycle.get_plugin_metadata(plugin_name)

    info: Dict[str, Any] = {
        "name": plugin_name,
        "state": state.value,
        "lifecycle_metadata": metadata,
    }

    if plugin:
        info.update(
            {
                "version": plugin.metadata.version,
                "description": plugin.metadata.description,
                "author": plugin.metadata.author,
                "plugin_dependencies": plugin.metadata.plugin_dependencies,
                "python_dependencies": plugin.metadata.python_dependencies,
                "required_resources": plugin.metadata.required_resources,
                "optional_resources": plugin.metadata.optional_resources,
                "min_core_version": plugin.metadata.min_core_version,
                "max_core_version": plugin.metadata.max_core_version,
                "homepage": plugin.metadata.homepage,
                "license": plugin.metadata.license,
                "tags": plugin.metadata.tags,
            }
        )

    return info


@router.post("/{plugin_name}/enable", response_model=PluginActionResponse)
async def enable_plugin(plugin_name: str, request: PluginEnableRequest):
    """
    Enable a disabled plugin.

    Args:
        plugin_name: Name of the plugin to enable
        request: Enable request with optional configuration

    Returns:
        Action response with success status
    """
    controller = get_controller()

    success = await controller.enable_plugin(plugin_name, request.config)

    state = controller.lifecycle.get_state(plugin_name)

    return PluginActionResponse(
        success=success,
        message=f"Plugin '{plugin_name}' {'enabled successfully' if success else 'failed to enable'}",
        plugin_name=plugin_name,
        state=state.value if state else None,
    )


@router.post("/{plugin_name}/disable", response_model=PluginActionResponse)
async def disable_plugin(plugin_name: str):
    """
    Disable an active plugin.

    Args:
        plugin_name: Name of the plugin to disable

    Returns:
        Action response with success status
    """
    controller = get_controller()

    success = await controller.disable_plugin(plugin_name)

    state = controller.lifecycle.get_state(plugin_name)

    return PluginActionResponse(
        success=success,
        message=f"Plugin '{plugin_name}' {'disabled successfully' if success else 'failed to disable'}",
        plugin_name=plugin_name,
        state=state.value if state else None,
    )


@router.post("/{plugin_name}/reload", response_model=PluginActionResponse)
async def reload_plugin(plugin_name: str, request: PluginReloadRequest):
    """
    Reload a plugin (hot-reload).

    Args:
        plugin_name: Name of the plugin to reload
        request: Reload request with optional new configuration

    Returns:
        Action response with success status
    """
    controller = get_controller()

    success = await controller.reload_plugin(plugin_name, request.config)

    state = controller.lifecycle.get_state(plugin_name)

    return PluginActionResponse(
        success=success,
        message=f"Plugin '{plugin_name}' {'reloaded successfully' if success else 'failed to reload'}",
        plugin_name=plugin_name,
        state=state.value if state else None,
    )


@router.post("/reload-all", response_model=Dict[str, Any])
async def reload_all_plugins():
    """
    Reload all active plugins.

    Returns:
        Dictionary mapping plugin names to reload status
    """
    controller = get_controller()

    results = await controller.reload_all_plugins()

    success_count = sum(1 for success in results.values() if success)
    total_count = len(results)

    return {
        "results": results,
        "summary": {
            "total": total_count,
            "succeeded": success_count,
            "failed": total_count - success_count,
        },
    }


@router.get("/status/overview", response_model=PluginStatusResponse)
async def get_plugin_status():
    """
    Get overview of plugin system status.

    Returns:
        Lifecycle summary and dependency graph
    """
    controller = get_controller()

    status_data = controller.get_reload_status()

    return PluginStatusResponse(
        lifecycle=status_data["lifecycle"],
        dependency_graph=status_data["dependency_graph"],
    )


@router.get("/{plugin_name}/dependents")
async def get_plugin_dependents(plugin_name: str):
    """
    Get list of plugins that depend on this plugin.

    Args:
        plugin_name: Name of the plugin

    Returns:
        List of dependent plugin names
    """
    controller = get_controller()

    state = controller.lifecycle.get_state(plugin_name)
    if not state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plugin '{plugin_name}' not found",
        )

    dependents = controller._find_dependent_plugins(plugin_name)

    return {
        "plugin_name": plugin_name,
        "dependents": dependents,
        "count": len(dependents),
    }


# === Phase 3: Metrics & Monitoring Endpoints ===


@router.get("/metrics/{plugin_name}")
async def get_plugin_metrics(plugin_name: str):
    """
    Get detailed metrics for a specific plugin.

    Args:
        plugin_name: Name of the plugin

    Returns:
        Plugin metrics including timing, errors, state history
    """
    metrics_collector = get_metrics_collector()
    plugin_metrics = metrics_collector.get_plugin_metrics(plugin_name)

    if not plugin_metrics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No metrics found for plugin '{plugin_name}'",
        )

    return plugin_metrics


@router.get("/metrics/system/overview")
async def get_system_metrics():
    """
    Get system-wide plugin metrics.

    Returns:
        Aggregated metrics across all plugins
    """
    metrics_collector = get_metrics_collector()
    return metrics_collector.get_system_metrics()


@router.get("/metrics/system/performance")
async def get_performance_metrics():
    """
    Get performance summary for all plugins.

    Returns:
        Performance statistics (load times, reload times, error rates)
    """
    metrics_collector = get_metrics_collector()
    return metrics_collector.get_performance_summary()


@router.get("/metrics/all")
async def get_all_metrics():
    """
    Get metrics for all tracked plugins.

    Returns:
        Dictionary mapping plugin names to their metrics
    """
    metrics_collector = get_metrics_collector()
    return metrics_collector.get_all_metrics()


@router.delete("/metrics/{plugin_name}")
async def reset_plugin_metrics(plugin_name: str):
    """
    Reset metrics for a specific plugin.

    Args:
        plugin_name: Name of the plugin

    Returns:
        Success message
    """
    metrics_collector = get_metrics_collector()
    metrics_collector.reset_metrics(plugin_name)

    return {
        "success": True,
        "message": f"Metrics reset for plugin '{plugin_name}'",
    }


@router.delete("/metrics/system/reset")
async def reset_all_metrics():
    """
    Reset all plugin metrics.

    Returns:
        Success message
    """
    metrics_collector = get_metrics_collector()
    metrics_collector.reset_metrics()

    return {
        "success": True,
        "message": "All plugin metrics reset",
    }
