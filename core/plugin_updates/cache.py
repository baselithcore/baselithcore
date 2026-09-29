"""On-disk cache for the update checker: last report, notified set, tarballs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import CheckReport

_REPORT_FILE = "last_check.json"
_NOTIFIED_FILE = "notified.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class UpdateCache:
    """Persists the last :class:`CheckReport` and verified release tarballs."""

    def __init__(self, root: Path) -> None:
        """Create a cache rooted at ``root`` (created lazily)."""
        self._root = root

    @property
    def root(self) -> Path:
        """The cache directory."""
        return self._root

    def load(self) -> CheckReport | None:
        """Return the last saved report, or None when absent or unreadable."""
        try:
            return CheckReport.model_validate_json(
                (self._root / _REPORT_FILE).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def save(self, report: CheckReport) -> None:
        """Atomically persist ``report``."""
        _atomic_write(self._root / _REPORT_FILE, report.model_dump_json())

    def load_notified(self) -> set[str]:
        """Return the ``plugin@version`` keys already announced."""
        try:
            data = json.loads((self._root / _NOTIFIED_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {str(item) for item in data} if isinstance(data, list) else set()

    def save_notified(self, keys: set[str]) -> None:
        """Atomically persist the announced keys."""
        _atomic_write(self._root / _NOTIFIED_FILE, json.dumps(sorted(keys)))

    def tarball_path(self, plugin: str, version: str) -> Path:
        """Where the verified tarball of ``plugin`` ``version`` is kept."""
        return self._root / "tarballs" / f"{plugin}-{version}.tar.gz"


__all__ = ["UpdateCache"]
