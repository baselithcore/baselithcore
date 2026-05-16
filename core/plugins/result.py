"""
Standard result envelope for plugin skills, tools, and handlers.

Defines ``SkillResult``: the canonical structured return type used by
tool implementations across plugins, MCP tools, and orchestration handlers.
Raw strings are forbidden — every result carries explicit success / data /
error fields. The envelope separates:

- ``success`` / ``error_code`` for deterministic branching.
- ``message`` for human-readable status.
- ``data`` for the full payload (consumed by downstream code).
- ``snapshot`` for an LLM-safe preview (first ``SNAPSHOT_MAX_CHARS`` chars
  of the payload) so the model receives bounded context.

Adoption is opt-in: existing plain-dict returns remain valid. New tools
and handlers should return ``SkillResult`` and orchestration code can
detect it via ``isinstance``.
"""

from __future__ import annotations

import json
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

SNAPSHOT_MAX_CHARS: Final[int] = 500


class SkillResult(BaseModel):
    """Structured envelope for a single skill/tool/handler invocation."""

    model_config = ConfigDict(frozen=True)

    success: bool
    message: str = ""
    data: Any = None
    snapshot: str | None = None
    error_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _make_snapshot(data: Any) -> str | None:
    """Return a bounded textual preview of ``data`` for LLM consumption."""
    if data is None:
        return None
    if isinstance(data, str):
        text = data
    else:
        try:
            text = json.dumps(data, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(data)
    if len(text) <= SNAPSHOT_MAX_CHARS:
        return text
    return text[:SNAPSHOT_MAX_CHARS] + "…"


def ok(
    data: Any = None,
    message: str = "",
    *,
    snapshot: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SkillResult:
    """Build a successful ``SkillResult``."""
    return SkillResult(
        success=True,
        message=message,
        data=data,
        snapshot=snapshot if snapshot is not None else _make_snapshot(data),
        error_code=None,
        metadata=metadata or {},
    )


def fail(
    message: str,
    *,
    error_code: str = "skill_error",
    data: Any = None,
    metadata: dict[str, Any] | None = None,
) -> SkillResult:
    """Build a failed ``SkillResult``."""
    return SkillResult(
        success=False,
        message=message,
        data=data,
        snapshot=_make_snapshot(data),
        error_code=error_code,
        metadata=metadata or {},
    )


def partial(
    data: Any,
    message: str,
    *,
    error_code: str = "skill_partial",
    metadata: dict[str, Any] | None = None,
) -> SkillResult:
    """Build a ``SkillResult`` representing a partial / degraded success."""
    return SkillResult(
        success=False,
        message=message,
        data=data,
        snapshot=_make_snapshot(data),
        error_code=error_code,
        metadata={**(metadata or {}), "partial": True},
    )
