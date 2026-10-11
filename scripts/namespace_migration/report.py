"""Per-category accounting of what the codemod changed and what it left alone."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

#: Categories that ``--check`` treats as violations when found still pending.
#: Everything else (``unresolved_*``, ``kept_*``) is advisory.
VIOLATION_PREFIXES: tuple[str, ...] = (
    "import_",
    "dotted_",
    "path_",
    "module_name_",
    "prefix_",
    "fstring_",
    "tooling_",
    "targeted_",
    "md_",
    "move_",
)


@dataclass
class FileResult:
    """What one file contributed.

    Attributes:
        rel: Repo-relative path.
        new_text: Rewritten content, or ``None`` when unchanged.
        counts: Rewrites applied, by category.
        unresolved: ``(category, snippet)`` pairs left for a human.
        error: Parse failure message, if the file could not be processed.
    """

    rel: str
    new_text: str | None = None
    counts: Counter[str] = field(default_factory=Counter)
    unresolved: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None


@dataclass
class Report:
    """Aggregate over every file, deterministic in content and order."""

    counts: Counter[str] = field(default_factory=Counter)
    files_changed: dict[str, Counter[str]] = field(default_factory=dict)
    unresolved: list[tuple[str, str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    files_scanned: int = 0
    moved: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def add(self, result: FileResult) -> None:
        """Fold one file's result in."""
        self.files_scanned += 1
        if result.error:
            self.errors.append((result.rel, result.error))
        if result.counts:
            self.counts.update(result.counts)
        if result.new_text is not None:
            self.files_changed[result.rel] = result.counts
        for cat, snippet in result.unresolved:
            self.unresolved.append((result.rel, cat, snippet))

    def violations(self) -> int:
        """Number of pending rewrites that ``--check`` must fail on."""
        total = sum(
            n for cat, n in self.counts.items() if cat.startswith(VIOLATION_PREFIXES)
        )
        return total + len(self.moved)

    def rewrite_total(self) -> int:
        """Rewrites applied (or pending), excluding advisory ``kept_*`` counters."""
        return sum(n for cat, n in self.counts.items() if not cat.startswith("kept_"))

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form, keys sorted for a stable diff."""
        return {
            "files_scanned": self.files_scanned,
            "files_changed": len(self.files_changed),
            "counts": dict(sorted(self.counts.items())),
            "moves": self.moved,
            "unresolved": [
                {"file": f, "category": c, "text": t}
                for f, c, t in sorted(self.unresolved)
            ],
            "errors": [{"file": f, "error": e} for f, e in sorted(self.errors)],
            "elapsed_s": round(self.elapsed_s, 2),
        }

    def render(self, *, limit: int = 40) -> str:
        """Human summary: counts by category, then the unresolved list."""
        lines = [
            f"files scanned: {self.files_scanned}",
            f"files changed: {len(self.files_changed)}",
            f"elapsed: {self.elapsed_s:.1f}s",
            "",
            "rewrites by category:",
        ]
        for cat, n in sorted(self.counts.items()):
            lines.append(f"  {cat:<32} {n:>7}")
        if self.moved:
            lines.append("")
            lines.append("moves:")
            lines.extend(f"  {m}" for m in self.moved)
        by_cat = Counter(c for _, c, _ in self.unresolved)
        lines.append("")
        lines.append(f"unresolved / advisory: {len(self.unresolved)}")
        for cat, n in sorted(by_cat.items()):
            lines.append(f"  {cat:<32} {n:>7}")
        for f, c, t in sorted(self.unresolved)[:limit]:
            lines.append(f"    {f}: [{c}] {t[:100]!r}")
        if len(self.unresolved) > limit:
            lines.append(f"    ... {len(self.unresolved) - limit} more (use --json)")
        if self.errors:
            lines.append("")
            lines.append(f"parse errors: {len(self.errors)}")
            lines.extend(f"  {f}: {e[:120]}" for f, e in sorted(self.errors))
        return "\n".join(lines)

    def dumps(self) -> str:
        """Serialised JSON report."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)


__all__ = ["VIOLATION_PREFIXES", "FileResult", "Report"]
