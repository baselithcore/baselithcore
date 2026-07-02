"""
Load prompt versions from a directory of Markdown files with YAML front matter.

Each ``*.md`` file declares one prompt version: the front matter carries the
metadata (name, version, labels, …) and the body is the template. This keeps
prompts reviewable as plain files (diff-friendly, version-controlled) while the
registry serves them at runtime.

Example ``prompts/greet.md``::

    ---
    name: greet
    version: "2"
    labels: [production]
    description: Friendly greeting
    variables: [name, product]
    ---
    Hello {{ name }}, welcome to {{ product }}.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.observability.logging import get_logger
from core.prompts.registry import PromptRegistry
from core.prompts.types import PromptError, PromptVersion

logger = get_logger(__name__)

_FRONT_MATTER = "---"


class PromptLoadError(PromptError):
    """A prompt file was malformed."""


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown document into (front-matter dict, body)."""
    stripped = text.lstrip()
    if not stripped.startswith(_FRONT_MATTER):
        raise PromptLoadError("Missing YAML front matter (expected leading '---')")
    # Drop the opening fence, then split on the closing one.
    rest = stripped[len(_FRONT_MATTER) :]
    end = rest.find("\n" + _FRONT_MATTER)
    if end == -1:
        raise PromptLoadError("Unterminated YAML front matter (missing closing '---')")
    fm_text = rest[:end]
    # Trim the blank lines around the body — the leading newline after the
    # closing fence and the trailing newline most editors append are artifacts,
    # not part of the prompt.
    body = rest[end + len("\n" + _FRONT_MATTER) :].strip("\n")
    meta = yaml.safe_load(fm_text) or {}
    if not isinstance(meta, dict):
        raise PromptLoadError("Front matter must be a mapping")
    return meta, body


def parse_prompt_file(path: Path) -> PromptVersion:
    """Parse a single Markdown prompt file into a :class:`PromptVersion`."""
    meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
    name = meta.get("name") or path.stem
    return PromptVersion(
        name=str(name),
        version=str(meta.get("version", "1")),
        template=body,
        description=meta.get("description"),
        labels=set(meta.get("labels", []) or []),
        variables=[str(v) for v in (meta.get("variables", []) or [])],
        metadata=meta.get("metadata", {}) or {},
    )


def load_prompts_from_dir(
    registry: PromptRegistry, directory: str | Path
) -> list[PromptVersion]:
    """Register every ``*.md`` prompt under ``directory`` into ``registry``.

    Files that fail to parse are logged and skipped (one bad file does not block
    the rest). Returns the successfully loaded versions.
    """
    root = Path(directory)
    if not root.is_dir():
        logger.warning("prompt_dir_missing", extra={"directory": str(root)})
        return []
    loaded: list[PromptVersion] = []
    for path in sorted(root.rglob("*.md")):
        try:
            pv = parse_prompt_file(path)
        except (PromptError, yaml.YAMLError, OSError) as exc:
            logger.warning(
                "prompt_file_skipped",
                extra={"file": str(path), "error": str(exc)},
            )
            continue
        registry.store.put(pv)
        loaded.append(pv)
    logger.info("prompts_loaded", extra={"count": len(loaded), "dir": str(root)})
    return loaded
