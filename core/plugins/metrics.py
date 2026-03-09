"""Plugin lifecycle metrics collection and reporting."""

from __future__ import annotations

import time

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, cast
from core.observability.logging import get_logger

from .lifecycle import PluginState

logger = get_logger(__name__)


@dataclass
class PluginMetrics:
    """Metrics for a single plugin."""

    plugin_name: str

    # Lifecycle counts
    load_count: int = 0
    reload_count: int = 0
    enable_count: int = 0
    disable_count: int = 0
    failure_count: int = 0

    # Timing metrics (milliseconds)
    total_load_time_ms: float = 0.0
    avg_load_time_ms: float = 0.0
    min_load_time_ms: float = float("inf")
    max_load_time_ms: float = 0.0

    total_reload_time_ms: float = 0.0
    avg_reload_time_ms: float = 0.0

    # State duration tracking
    time_in_active_ms: float = 0.0
    time_in_disabled_ms: float = 0.0
    time_in_failed_ms: float = 0.0

    # Error tracking
    last_error: Optional[str] = None
    last_error_timestamp: Optional[datetime] = None
    error_history: List[Dict[str, Any]] = field(default_factory=list)

    # State change tracking
    state_changes: List[Dict[str, Any]] = field(default_factory=list)
    current_state: Optional[PluginState] = None
    state_entered_at: Optional[datetime] = None

    # Resource usage (placeholder for future integration)
    memory_usage_bytes: Optional[int] = None
    cpu_usage_percent: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "plugin_name": self.plugin_name,
            "lifecycle_counts": {
                "load": self.load_count,
                "reload": self.reload_count,
                "enable": self.enable_count,
                "disable": self.disable_count,
                "failure": self.failure_count,
            },
            "timing": {
                "load": {
                    "total_ms": self.total_load_time_ms,
                    "avg_ms": self.avg_load_time_ms,
                    "min_ms": self.min_load_time_ms
                    if self.min_load_time_ms != float("inf")
                    else None,
                    "max_ms": self.max_load_time_ms,
                },
                "reload": {
                    "total_ms": self.total_reload_time_ms,
                    "avg_ms": self.avg_reload_time_ms,
                },
            },
            "state_duration": {
                "active_ms": self.time_in_active_ms,
                "disabled_ms": self.time_in_disabled_ms,
                "failed_ms": self.time_in_failed_ms,
            },
            "errors": {
                "last_error": self.last_error,
                "last_error_timestamp": self.last_error_timestamp.isoformat()
                if self.last_error_timestamp
                else None,
                "total_errors": len(self.error_history),
                "recent_errors": self.error_history[-5:],  # Last 5 errors
            },
            "current_state": {
                "state": self.current_state.value if self.current_state else None,
                "entered_at": self.state_entered_at.isoformat()
                if self.state_entered_at
                else None,
                "duration_ms": self._calculate_state_duration(),
            },
            "resources": {
                "memory_bytes": self.memory_usage_bytes,
                "cpu_percent": self.cpu_usage_percent,
            },
        }

    def _calculate_state_duration(self) -> Optional[float]:
        """Calculate how long plugin has been in current state."""
        if not self.state_entered_at:
            return None

        delta = datetime.now(timezone.utc) - self.state_entered_at
        return delta.total_seconds() * 1000


class PluginMetricsCollector:
    """
    Collects and aggregates metrics for all plugins.

    Tracks:
    - Lifecycle events (load, reload, enable, disable)
    - Timing metrics (load time, reload time)
    - State durations
    - Error rates
    - Performance trends
    """

    def __init__(self):
        """Initialize metrics collector."""
        self._metrics: Dict[str, PluginMetrics] = {}
        self._system_start_time = datetime.now(timezone.utc)

        # Aggregated system metrics
        self._total_operations = 0
        self._total_failures = 0
        self._operation_history: List[Dict[str, Any]] = []

    def get_or_create_metrics(self, plugin_name: str) -> PluginMetrics:
        """Get metrics for a plugin, creating if not exists."""
        if plugin_name not in self._metrics:
            self._metrics[plugin_name] = PluginMetrics(plugin_name=plugin_name)
        return self._metrics[plugin_name]

    def record_load_start(self, plugin_name: str) -> float:
        """
        Record start of load operation.

        Returns:
            Start timestamp for timing
        """
        return time.time()

    def record_load_complete(
        self, plugin_name: str, start_time: float, success: bool = True
    ) -> None:
        """
        Record completion of load operation.

        Args:
            plugin_name: Name of plugin
            start_time: Start timestamp from record_load_start
            success: Whether load succeeded
        """
        metrics = self.get_or_create_metrics(plugin_name)

        duration_ms = (time.time() - start_time) * 1000

        metrics.load_count += 1
        metrics.total_load_time_ms += duration_ms
        metrics.avg_load_time_ms = metrics.total_load_time_ms / metrics.load_count
        metrics.min_load_time_ms = min(metrics.min_load_time_ms, duration_ms)
        metrics.max_load_time_ms = max(metrics.max_load_time_ms, duration_ms)

        if not success:
            metrics.failure_count += 1
            self._total_failures += 1

        self._total_operations += 1

        # Record in history
        self._operation_history.append(
            {
                "plugin": plugin_name,
                "operation": "load",
                "success": success,
                "duration_ms": duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Keep only last 1000 operations
        if len(self._operation_history) > 1000:
            self._operation_history = self._operation_history[-1000:]

    def record_reload_start(self, plugin_name: str) -> float:
        """Record start of reload operation."""
        return time.time()

    def record_reload_complete(
        self, plugin_name: str, start_time: float, success: bool = True
    ) -> None:
        """Record completion of reload operation."""
        metrics = self.get_or_create_metrics(plugin_name)

        duration_ms = (time.time() - start_time) * 1000

        metrics.reload_count += 1
        metrics.total_reload_time_ms += duration_ms
        metrics.avg_reload_time_ms = metrics.total_reload_time_ms / metrics.reload_count

        if not success:
            metrics.failure_count += 1
            self._total_failures += 1

        self._total_operations += 1

        self._operation_history.append(
            {
                "plugin": plugin_name,
                "operation": "reload",
                "success": success,
                "duration_ms": duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def record_enable(self, plugin_name: str) -> None:
        """Record plugin enable event."""
        metrics = self.get_or_create_metrics(plugin_name)
        metrics.enable_count += 1
        self._total_operations += 1

    def record_disable(self, plugin_name: str) -> None:
        """Record plugin disable event."""
        metrics = self.get_or_create_metrics(plugin_name)
        metrics.disable_count += 1
        self._total_operations += 1

    def record_error(self, plugin_name: str, error: Exception) -> None:
        """
        Record a plugin error.

        Args:
            plugin_name: Name of plugin
            error: Exception that occurred
        """
        metrics = self.get_or_create_metrics(plugin_name)

        error_info = {
            "error": str(error),
            "type": type(error).__name__,
            "timestamp": datetime.now(timezone.utc),
        }

        metrics.last_error = str(error)
        metrics.last_error_timestamp = cast(datetime, error_info["timestamp"])
        metrics.error_history.append(error_info)

        # Keep only last 50 errors per plugin
        if len(metrics.error_history) > 50:
            metrics.error_history = metrics.error_history[-50:]

        metrics.failure_count += 1
        self._total_failures += 1

    def record_state_change(
        self, plugin_name: str, old_state: Optional[PluginState], new_state: PluginState
    ) -> None:
        """
        Record a state transition.

        Args:
            plugin_name: Name of plugin
            old_state: Previous state
            new_state: New state
        """
        metrics = self.get_or_create_metrics(plugin_name)
        now = datetime.now(timezone.utc)

        # Calculate duration in previous state
        if old_state and metrics.state_entered_at:
            duration = (now - metrics.state_entered_at).total_seconds() * 1000

            # Accumulate time in state
            if old_state == PluginState.ACTIVE:
                metrics.time_in_active_ms += duration
            elif old_state == PluginState.DISABLED:
                metrics.time_in_disabled_ms += duration
            elif old_state == PluginState.FAILED:
                metrics.time_in_failed_ms += duration

        # Record transition
        metrics.state_changes.append(
            {
                "from": old_state.value if old_state else None,
                "to": new_state.value,
                "timestamp": now.isoformat(),
            }
        )

        # Keep only last 100 state changes
        if len(metrics.state_changes) > 100:
            metrics.state_changes = metrics.state_changes[-100:]

        # Update current state
        metrics.current_state = new_state
        metrics.state_entered_at = now

    def get_plugin_metrics(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """
        Get metrics for a specific plugin.

        Args:
            plugin_name: Name of plugin

        Returns:
            Metrics dictionary or None if plugin not found
        """
        if plugin_name not in self._metrics:
            return None

        return self._metrics[plugin_name].to_dict()

    def get_all_metrics(self) -> Dict[str, Any]:
        """Get metrics for all plugins."""
        return {name: metrics.to_dict() for name, metrics in self._metrics.items()}

    def get_system_metrics(self) -> Dict[str, Any]:
        """Get aggregated system-wide metrics."""
        uptime = datetime.now(timezone.utc) - self._system_start_time

        # Calculate success rate
        success_rate = (
            (self._total_operations - self._total_failures)
            / self._total_operations
            * 100
            if self._total_operations > 0
            else 100.0
        )

        # Recent operations (last hour)
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_ops = [
            op
            for op in self._operation_history
            if datetime.fromisoformat(op["timestamp"]) > one_hour_ago
        ]

        return {
            "uptime_seconds": uptime.total_seconds(),
            "total_operations": self._total_operations,
            "total_failures": self._total_failures,
            "success_rate_percent": success_rate,
            "total_plugins_tracked": len(self._metrics),
            "recent_operations": {
                "last_hour": len(recent_ops),
                "operations": recent_ops[-20:],  # Last 20
            },
            "plugin_summary": {
                "active": sum(
                    1
                    for m in self._metrics.values()
                    if m.current_state == PluginState.ACTIVE
                ),
                "disabled": sum(
                    1
                    for m in self._metrics.values()
                    if m.current_state == PluginState.DISABLED
                ),
                "failed": sum(
                    1
                    for m in self._metrics.values()
                    if m.current_state == PluginState.FAILED
                ),
            },
        }

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary across all plugins."""
        all_load_times = [
            m.avg_load_time_ms for m in self._metrics.values() if m.load_count > 0
        ]
        all_reload_times = [
            m.avg_reload_time_ms for m in self._metrics.values() if m.reload_count > 0
        ]

        return {
            "load_performance": {
                "avg_ms": sum(all_load_times) / len(all_load_times)
                if all_load_times
                else 0,
                "min_ms": min(all_load_times) if all_load_times else 0,
                "max_ms": max(all_load_times) if all_load_times else 0,
            },
            "reload_performance": {
                "avg_ms": sum(all_reload_times) / len(all_reload_times)
                if all_reload_times
                else 0,
            },
            "error_rate": {
                "total_errors": self._total_failures,
                "plugins_with_errors": sum(
                    1 for m in self._metrics.values() if m.failure_count > 0
                ),
            },
        }

    def reset_metrics(self, plugin_name: Optional[str] = None) -> None:
        """
        Reset metrics.

        Args:
            plugin_name: If provided, reset only this plugin's metrics.
                        Otherwise, reset all metrics.
        """
        if plugin_name:
            if plugin_name in self._metrics:
                self._metrics[plugin_name] = PluginMetrics(plugin_name=plugin_name)
        else:
            self._metrics.clear()
            self._total_operations = 0
            self._total_failures = 0
            self._operation_history.clear()
            self._system_start_time = datetime.now(timezone.utc)


# Global metrics collector instance
_metrics_collector: Optional[PluginMetricsCollector] = None


def get_metrics_collector() -> PluginMetricsCollector:
    """Get or create global metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = PluginMetricsCollector()
    return _metrics_collector
