"""Persistent runtime overlay for ``ComputerUseConfig`` and ``StealthConfig``.

The base configuration ships in ``configs/plugins.yaml`` and is loaded once
at boot. The dashboard needs to mutate ``computer_use`` and ``stealth`` at
runtime without editing files on disk; this store persists those overrides
under ``<state>/runtime_config.json`` and applies them on top of the boot
config when the agent is rebuilt.

Threading: a single ``threading.Lock`` serializes mutations.
Atomicity: writes go through ``.tmp`` + ``os.replace``.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger

from .computer_use import ComputerUseConfig
from .types import StealthConfig

logger = get_logger(__name__)

_FILENAME = "runtime_config.json"


class RuntimeConfigStore:
    """JSON-backed overlay for ``computer_use`` and ``stealth`` blocks."""

    def __init__(self, state_dir: str | Path) -> None:
        self._path = Path(state_dir) / _FILENAME
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = self._read()

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return {}
            return {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "baselithbot_runtime_config_read_failed",
                path=str(self._path),
                error=str(exc),
            )
            return {}

    def _write_locked(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._cache, fh, indent=2, sort_keys=True)
        os.replace(tmp, self._path)

    def get_computer_use(self, base: ComputerUseConfig) -> ComputerUseConfig:
        """Return base merged with any persisted ``computer_use`` overrides."""
        with self._lock:
            override = dict(self._cache.get("computer_use", {}))
        if not override:
            return base
        merged = base.model_dump()
        merged.update(override)
        return ComputerUseConfig(**merged)

    def get_stealth(self, base: StealthConfig) -> StealthConfig:
        """Return base merged with any persisted ``stealth`` overrides."""
        with self._lock:
            override = dict(self._cache.get("stealth", {}))
        if not override:
            return base
        merged = base.model_dump()
        merged.update(override)
        return StealthConfig(**merged)

    def set_computer_use(self, config: ComputerUseConfig) -> ComputerUseConfig:
        """Persist a new ``ComputerUseConfig`` and return the validated copy."""
        with self._lock:
            self._cache["computer_use"] = config.model_dump()
            self._write_locked()
        logger.info(
            "baselithbot_runtime_config_computer_use_updated",
            enabled=config.enabled,
            allow_shell=config.allow_shell,
            allow_filesystem=config.allow_filesystem,
            allowed_shell_commands=len(config.allowed_shell_commands),
        )
        return config

    def set_stealth(self, config: StealthConfig) -> StealthConfig:
        """Persist a new ``StealthConfig`` and return the validated copy."""
        with self._lock:
            self._cache["stealth"] = config.model_dump()
            self._write_locked()
        logger.info(
            "baselithbot_runtime_config_stealth_updated",
            enabled=config.enabled,
            rotate_user_agent=config.rotate_user_agent,
            mask_webdriver=config.mask_webdriver,
        )
        return config

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the current overlay for diagnostics."""
        with self._lock:
            return {k: dict(v) for k, v in self._cache.items()}


__all__ = ["RuntimeConfigStore"]
