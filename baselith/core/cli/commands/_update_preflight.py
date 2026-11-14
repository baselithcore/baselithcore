"""``baselith update preflight``: whether an update can be installed now.

Read-only: it reads the saved report, the run store, the task queue and the
disk through the same builder the API serves, and never loads plugin code,
contacts GitHub or writes anything. Exit 1 when any item blocks, or when an
``info`` item says the release is not installed from here.
"""

from __future__ import annotations

import json
import logging

from baselith.core.config.plugin_updates import get_plugin_update_config
from baselith.core.plugin_updates.preflight import PreflightItem, PreflightLevel
from baselith.core.plugin_updates.service import PluginUpdateService

logger = logging.getLogger(__name__)


def _preflight_inputs(subject: str, version: str | None) -> list[PreflightItem]:
    """The pre-flight items, built exactly as the server builds them."""
    service = PluginUpdateService(get_plugin_update_config())
    return service.preflight(subject, version)


def render_preflight(items: list[PreflightItem]) -> list[str]:
    """One ``[level] code: message`` line per item (pure)."""
    return [f"[{i.level.value}] {i.code}: {i.message}" for i in items]


def run_preflight(subject: str, version: str | None, fmt: str) -> int:
    """Print the pre-flight of ``subject``; 1 unless it can be installed now."""
    try:
        items = _preflight_inputs(subject, version)
    except Exception as exc:  # a diagnostic must report, not crash
        logger.debug("preflight failed", exc_info=True)
        print(f"preflight failed: {type(exc).__name__}")
        return 1
    if fmt == "json":
        print(json.dumps([i.model_dump(mode="json") for i in items], indent=2))
    else:
        print("\n".join(render_preflight(items)))
    stops = (PreflightLevel.BLOCK, PreflightLevel.INFO)
    return 1 if any(i.level in stops for i in items) else 0


__all__ = ["render_preflight", "run_preflight"]
