"""Computer Use configuration + capability gating + audit log.

Implements the Anthropic Computer Use safety model:
    - opt-in only (``enabled=False`` by default)
    - explicit capability flags (mouse / keyboard / screenshot / shell / fs)
    - shell command allowlist (exact match or prefix)
    - filesystem operations scoped to a single root directory
    - JSON-Lines audit log of every privileged action
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from core.observability.logging import get_logger

logger = get_logger(__name__)


class ComputerUseError(RuntimeError):
    """Raised when a Computer Use action is denied by configuration."""


class ComputerUseConfig(BaseModel):
    """Capability gates for Baselithbot OS-level actions."""

    enabled: bool = Field(default=False, description="Master switch — OFF by default.")
    allow_mouse: bool = Field(default=True)
    allow_keyboard: bool = Field(default=True)
    allow_screenshot: bool = Field(default=True)
    allow_shell: bool = Field(default=False)
    allow_filesystem: bool = Field(default=False)

    allowed_shell_commands: list[str] = Field(
        default_factory=list,
        description=(
            "Exact-match or prefix-match allowlist for the first token of "
            "any shell command. Empty list disables shell entirely."
        ),
    )
    shell_timeout_seconds: float = Field(default=30.0, ge=1.0, le=600.0)

    filesystem_root: str | None = Field(
        default=None,
        description=(
            "Absolute path under which read/write/list operations are confined."
        ),
    )
    filesystem_max_bytes: int = Field(default=10_000_000, ge=1)

    audit_log_path: str | None = Field(default=None)

    def require_enabled(self, capability: str) -> None:
        """Raise ``ComputerUseError`` if Computer Use or the capability is off."""
        if not self.enabled:
            raise ComputerUseError(
                "Computer Use is disabled; set computer_use.enabled=true to opt in."
            )
        flag = f"allow_{capability}"
        if not getattr(self, flag, False):
            raise ComputerUseError(f"capability '{capability}' is not allowed")


class AuditLogger:
    """JSON-Lines append-only audit log for privileged Computer Use actions."""

    def __init__(self, path: str | None) -> None:
        self._path = Path(path) if path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, action: str, **fields: Any) -> None:
        """Persist an audit entry; also emits a structured log line."""
        entry = {
            "ts": time.time(),
            "action": action,
            **fields,
        }
        logger.info("baselithbot_computer_use_audit", **entry)
        if self._path is None:
            return
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")


__all__ = [
    "ComputerUseConfig",
    "ComputerUseError",
    "AuditLogger",
]
