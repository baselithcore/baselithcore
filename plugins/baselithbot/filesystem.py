"""Filesystem access scoped under a single configured root directory.

Every read / write / list resolves the requested path through
``Path.resolve(strict=False)`` and asserts the result is contained in
``ComputerUseConfig.filesystem_root``. This blocks ``..`` traversal and
absolute-path escape.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .approvals import ApprovalGate, ApprovalStatus
from .computer_use import AuditLogger, ComputerUseConfig, ComputerUseError


class ScopedFileSystem:
    """Root-scoped read/write/list operations."""

    def __init__(
        self,
        config: ComputerUseConfig,
        audit: AuditLogger,
        approvals: ApprovalGate | None = None,
    ) -> None:
        self._config = config
        self._audit = audit
        self._approvals = approvals
        if config.filesystem_root:
            self._root: Path | None = Path(config.filesystem_root).resolve()
        else:
            self._root = None

    async def _gate(self, action: str, params: dict[str, Any]) -> None:
        """Mutating fs ops can require operator approval."""
        if self._approvals is None:
            return
        if "filesystem" not in self._config.require_approval_for:
            return
        req = await self._approvals.submit(
            capability="filesystem",
            action=action,
            params=params,
            timeout_seconds=self._config.approval_timeout_seconds,
        )
        if req.status != ApprovalStatus.APPROVED:
            self._audit.record(
                f"{action}.{req.status.value}",
                status=req.status.value,
                approval_id=req.id,
                **params,
            )
            raise ComputerUseError(
                f"operator {req.status.value} {action} (approval id={req.id})"
            )

    def _resolve(self, path: str) -> Path:
        if self._root is None:
            raise ComputerUseError(
                "filesystem_root is not configured; refusing path resolution"
            )
        if "\x00" in path:
            raise ComputerUseError("null byte in path is not allowed")
        candidate = (self._root / path).resolve(strict=False)
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ComputerUseError(
                f"path '{path}' escapes filesystem_root '{self._root}'"
            ) from exc
        # Reject symlinks anywhere on the resolved path that point outside the root.
        cursor = candidate
        while cursor != self._root and cursor.parent != cursor:
            if cursor.exists() and cursor.is_symlink():
                target = cursor.resolve(strict=False)
                try:
                    target.relative_to(self._root)
                except ValueError as exc:
                    raise ComputerUseError(
                        f"symlink '{cursor}' escapes filesystem_root"
                    ) from exc
            cursor = cursor.parent
        return candidate

    async def read(self, path: str) -> dict[str, Any]:
        self._config.require_enabled("filesystem")
        target = self._resolve(path)
        if not target.is_file():
            raise ComputerUseError(f"file not found: {path}")
        size = target.stat().st_size
        if size > self._config.filesystem_max_bytes:
            raise ComputerUseError(
                f"file exceeds max size ({size} > {self._config.filesystem_max_bytes})"
            )
        content = await asyncio.to_thread(target.read_text, "utf-8")
        self._audit.record("fs_read", path=str(target), bytes=size)
        return {"path": str(target), "content": content, "bytes": size}

    async def write(self, path: str, content: str) -> dict[str, Any]:
        self._config.require_enabled("filesystem")
        target = self._resolve(path)
        encoded = content.encode("utf-8")
        if len(encoded) > self._config.filesystem_max_bytes:
            raise ComputerUseError(
                f"content exceeds max size ({len(encoded)} > "
                f"{self._config.filesystem_max_bytes})"
            )
        await self._gate("fs_write", {"path": str(target), "bytes": len(encoded)})
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, encoded)
        self._audit.record("fs_write", path=str(target), bytes=len(encoded))
        return {"path": str(target), "bytes_written": len(encoded)}

    async def list_dir(self, path: str = ".") -> dict[str, Any]:
        self._config.require_enabled("filesystem")
        target = self._resolve(path)
        if not target.is_dir():
            raise ComputerUseError(f"not a directory: {path}")

        def _scan() -> list[dict[str, Any]]:
            return [
                {
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else None,
                }
                for child in sorted(target.iterdir())
            ]

        entries = await asyncio.to_thread(_scan)
        self._audit.record("fs_list", path=str(target), entries=len(entries))
        return {"path": str(target), "entries": entries}


__all__ = ["ScopedFileSystem"]
