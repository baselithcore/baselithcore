"""Computer Use configuration + capability gating + audit log.

Implements the Anthropic Computer Use safety model:
    - opt-in only (``enabled=False`` by default)
    - explicit capability flags (mouse / keyboard / screenshot / shell / fs)
    - shell command allowlist (exact match or prefix)
    - filesystem operations scoped to a single root directory
    - JSON-Lines audit log of every privileged action
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from core.observability.logging import get_logger

from .secret_redaction import redact_payload

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

    require_approval_for: list[str] = Field(
        default_factory=list,
        description=(
            "Capabilities that must be approved by an operator before execution. "
            "Valid entries: 'mouse', 'keyboard', 'screenshot', 'shell', 'filesystem'. "
            "An empty list disables the approval gate entirely."
        ),
    )
    approval_timeout_seconds: float = Field(
        default=120.0,
        ge=1.0,
        le=3600.0,
        description="Seconds to wait for operator approval before auto-denying.",
    )

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
    """JSON-Lines append-only audit log with batched flush + secret redaction."""

    def __init__(
        self,
        path: str | None,
        *,
        batch_size: int = 16,
        flush_interval_seconds: float = 5.0,
    ) -> None:
        self._path = Path(path) if path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._buffer: list[str] = []
        self._batch_size = max(1, int(batch_size))
        self._flush_interval = max(0.1, float(flush_interval_seconds))
        self._lock = threading.Lock()
        self._last_flush = time.time()

    def record(self, action: str, **fields: Any) -> None:
        """Persist an audit entry; also emits a structured log line.

        Sensitive keys (token / password / secret / api_key / webhook_url …)
        are redacted both from the structured log line and the JSONL file.
        """
        safe_fields = redact_payload(fields)
        entry = {
            "ts": time.time(),
            "action": action,
            **(
                safe_fields
                if isinstance(safe_fields, dict)
                else {"fields": safe_fields}
            ),
        }
        logger.info("baselithbot_computer_use_audit", **entry)
        if self._path is None:
            return
        line = json.dumps(entry, default=str) + "\n"
        with self._lock:
            self._buffer.append(line)
            should_flush = (
                len(self._buffer) >= self._batch_size
                or (time.time() - self._last_flush) >= self._flush_interval
            )
            if should_flush:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer or self._path is None:
            self._buffer.clear()
            self._last_flush = time.time()
            return
        with self._path.open("a", encoding="utf-8") as fh:
            fh.writelines(self._buffer)
        self._buffer.clear()
        self._last_flush = time.time()

    async def aclose(self) -> None:
        await asyncio.to_thread(self.flush)


__all__ = [
    "ComputerUseConfig",
    "ComputerUseError",
    "AuditLogger",
]
