"""
Few-shot example library.

In-context examples are the cheapest way to enforce output format and
reasoning style. The library indexes examples by task type so the persona
manager can splice the right ones into the system prompt at runtime.

Examples are immutable, version-controlled data; the loader treats YAML
or JSON files as the source of truth so non-engineers can edit them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Iterable

import yaml

DEFAULT_MAX_EXAMPLES_PER_TASK: Final[int] = 4


class FewShotLoadError(RuntimeError):
    """Raised when example data fails validation."""


@dataclass(frozen=True)
class FewShotExample:
    """A single input/output pair used in-context."""

    input: str
    output: str
    rationale: str | None = None
    tags: tuple[str, ...] = ()


@dataclass
class FewShotLibrary:
    """In-memory store of examples indexed by task type."""

    examples: dict[str, list[FewShotExample]] = field(default_factory=dict)

    def add(self, task_type: str, example: FewShotExample) -> None:
        """Append an example to a task bucket."""
        if not task_type:
            raise ValueError("task_type must be non-empty")
        self.examples.setdefault(task_type, []).append(example)

    def select(
        self,
        task_type: str,
        *,
        limit: int = DEFAULT_MAX_EXAMPLES_PER_TASK,
        tags: Iterable[str] | None = None,
    ) -> list[FewShotExample]:
        """Return up to ``limit`` examples for the task, optionally filtered by tags."""
        if limit <= 0:
            raise ValueError("limit must be > 0")
        items = self.examples.get(task_type, [])
        if tags:
            required = set(tags)
            items = [e for e in items if required.issubset(set(e.tags))]
        return items[:limit]

    def task_types(self) -> list[str]:
        return sorted(self.examples.keys())

    def render(
        self,
        task_type: str,
        *,
        limit: int = DEFAULT_MAX_EXAMPLES_PER_TASK,
        tags: Iterable[str] | None = None,
    ) -> str:
        """Render the selected examples as a Markdown few-shot block."""
        chosen = self.select(task_type, limit=limit, tags=tags)
        if not chosen:
            return ""
        parts: list[str] = []
        for idx, ex in enumerate(chosen, start=1):
            block = [
                f"### Example {idx}",
                "",
                "**Input:**",
                ex.input,
                "",
                "**Output:**",
                ex.output,
            ]
            if ex.rationale:
                block += ["", "**Rationale:**", ex.rationale]
            parts.append("\n".join(block))
        return "\n\n".join(parts)


def _parse_example(raw: object, source: Path | str) -> FewShotExample:
    """Validate one raw example payload."""
    if not isinstance(raw, dict):
        raise FewShotLoadError(
            f"{source}: each example must be a mapping, got {type(raw).__name__}"
        )
    inp = raw.get("input")
    out = raw.get("output")
    if not isinstance(inp, str) or not inp:
        raise FewShotLoadError(f"{source}: 'input' must be a non-empty string")
    if not isinstance(out, str) or not out:
        raise FewShotLoadError(f"{source}: 'output' must be a non-empty string")
    rationale = raw.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        raise FewShotLoadError(f"{source}: 'rationale' must be a string if present")
    raw_tags = raw.get("tags", [])
    if not isinstance(raw_tags, list) or not all(isinstance(t, str) for t in raw_tags):
        raise FewShotLoadError(f"{source}: 'tags' must be a list of strings")
    return FewShotExample(
        input=inp,
        output=out,
        rationale=rationale,
        tags=tuple(raw_tags),
    )


def load_library(path: Path | str) -> FewShotLibrary:
    """Load a library from a YAML or JSON file.

    Expected top-level structure::

        task_type:
          - input: "..."
            output: "..."
            rationale: "..." (optional)
            tags: ["..."]  (optional)
    """
    p = Path(path)
    if not p.exists():
        raise FewShotLoadError(f"library file does not exist: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise FewShotLoadError(
            f"{p}: top-level must be a mapping of task_type -> examples"
        )
    lib = FewShotLibrary()
    for task_type, examples in data.items():
        if not isinstance(task_type, str):
            raise FewShotLoadError(f"{p}: task_type keys must be strings")
        if not isinstance(examples, list):
            raise FewShotLoadError(f"{p}: examples for '{task_type}' must be a list")
        for raw in examples:
            lib.add(task_type, _parse_example(raw, p))
    return lib
