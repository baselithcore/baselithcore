"""On-disk cache for the update checker: last report, notified set, tarballs.

The cache is versioned. :data:`CACHE_FORMAT` 2 is the first whose verified
tarballs and ``available`` verdicts required the signed per-file release
manifest; anything an older checker left behind — a report without the format
field, the ``tarballs/`` directory — may describe a legacy release as
installable, so a report of an older format loads without its plugin
candidates ("no plugin report yet") and :meth:`UpdateCache.purge_legacy`
deletes the old tarball directory. Its core ``system`` notice is kept: it
never depended on a release manifest, and dropping it would hide a known
security notice after an upgrade — for as long as GitHub stays unreachable,
since a failed check carries the previous notice over.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .models import CheckReport

_REPORT_FILE = "last_check.json"
_NOTIFIED_FILE = "notified.json"
#: Bumped whenever a saved verdict or tarball of the previous checker could be
#: wrong under the current rules.
CACHE_FORMAT = 2
_FORMAT_KEY = "cache_format"
_TARBALL_DIR = f"tarballs-v{CACHE_FORMAT}"
_LEGACY_TARBALL_DIRS = ("tarballs",)


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
        """Return the last saved report, or None when absent or unreadable.

        A report saved by an older cache format (no or another
        ``cache_format``) keeps only ``checked_at`` and ``system``: its plugin
        verdicts predate the current rules, so ``candidates`` come back empty
        and the plugin ``error`` is cleared.
        """
        try:
            data: Any = json.loads(
                (self._root / _REPORT_FILE).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        fmt = data.pop(_FORMAT_KEY, None)
        if type(fmt) is not int or fmt != CACHE_FORMAT:
            data = {
                "checked_at": data.get("checked_at"),
                "candidates": [],
                "system": data.get("system"),
            }
        try:
            return CheckReport.model_validate(data)
        except ValueError:
            return None

    def save(self, report: CheckReport) -> None:
        """Atomically persist ``report``, stamped with :data:`CACHE_FORMAT`."""
        body = {_FORMAT_KEY: CACHE_FORMAT, **report.model_dump(mode="json")}
        _atomic_write(self._root / _REPORT_FILE, json.dumps(body))

    def purge_legacy(self) -> None:
        """Delete tarball directories an older cache format left behind.

        Raises:
            OSError: A directory exists but cannot be removed.
        """
        for name in _LEGACY_TARBALL_DIRS:
            path = self._root / name
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)

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
        return self._root / _TARBALL_DIR / f"{plugin}-{version}.tar.gz"


__all__ = ["CACHE_FORMAT", "UpdateCache"]
