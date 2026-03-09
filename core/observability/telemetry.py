"""
Telemetry Collector Module.

This module provides a thread-safe, high-performance counter for system events.
It serves as the primary mechanism for tracking internal metrics (e.g., chat
success rates, token usage, tool executions) and optionally exports them
to Prometheus for real-time monitoring.

Architecture:
- Thread Safety: Uses `threading.Lock` to ensure consistent state during increments.
- Prometheus Integration: Transparently increments a global Prometheus counter
  if the `prometheus_client` library is present.
- Snapshot Support: Allows external services to retrieve an atomic view
  of all counters and their update timestamps.
"""

from __future__ import annotations

import datetime
import threading
import time
from collections import Counter as PyCounter
from typing import Any, Dict, Optional, Type

# Optional Prometheus integration (soft dependency).
try:  # pragma: no cover
    from prometheus_client import Counter as PrometheusCounter  # type: ignore

    _PrometheusCounterType: Optional[Type[Any]] = PrometheusCounter
except Exception:  # pragma: no cover
    _PrometheusCounterType = None


class TelemetryCollector:
    """
    Centralized event tracker for BaselithCore.

    This collector maintains internal counters and timestamps in memory,
    providing a 'Pulse' of the agent's operational health.

    Usage:
        telemetry.increment("api_request_total")
        stats = telemetry.snapshot()
    """

    def __init__(self) -> None:
        """
        Initialize the collector state.
        """
        self._lock = threading.Lock()
        self._counters: PyCounter[str] = PyCounter()
        self._last_updated: Dict[str, float] = {}
        self._created_at = time.time()

        # Initialize the global Prometheus counter if available.
        self._prom_counter = (
            _PrometheusCounterType(
                "baselith_events_total",
                "Baselith core event telemetry.",
                ["name"],
            )
            if _PrometheusCounterType is not None
            else None
        )

    def increment(self, name: str, *, value: int = 1) -> None:
        """
        Atomically increment a named counter.

        Args:
            name: The event identifier (e.g., "llm_error").
            value: The amount to add. Defaults to 1.
        """
        if not name or value == 0:
            return

        with self._lock:
            self._counters[name] += value
            self._last_updated[name] = time.time()

        if self._prom_counter is not None and value > 0:
            self._prom_counter.labels(name=name).inc(value)

    def snapshot(self) -> Dict[str, Any]:
        """
        Capture the current state of all telemetry data.

        Returns:
            Dict: A nested dictionary containing:
                - created_at: ISO timestamp of collector start.
                - counters: Raw count values.
                - last_updated: ISO timestamps of the last increment per counter.
        """
        with self._lock:
            counters = dict(self._counters)
            last_updated = {
                key: datetime.datetime.fromtimestamp(ts).isoformat()
                for key, ts in self._last_updated.items()
            }
            created_at = datetime.datetime.fromtimestamp(self._created_at).isoformat()

        return {
            "created_at": created_at,
            "counters": counters,
            "last_updated": last_updated,
        }


# Global Singleton for system-wide telemetry access.
telemetry = TelemetryCollector()

__all__ = ["telemetry", "TelemetryCollector"]
