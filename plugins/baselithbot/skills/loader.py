"""Loader for AGENTS.md / SOUL.md / TOOLS.md injection bundles.

Mirrors the OpenClaw convention where each workspace can ship three
markdown files that are injected into the system prompt. The loader is
purely declarative — it returns the resolved text + paths so the caller
decides how to merge into the LLM prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel


class InjectionBundle(BaseModel):
    agents_md: str | None = None
    soul_md: str | None = None
    tools_md: str | None = None
    sources: dict[str, str] = {}

    def to_prompt_block(self) -> str:
        sections: list[str] = []
        if self.soul_md:
            sections.append(f"<soul>\n{self.soul_md.strip()}\n</soul>")
        if self.agents_md:
            sections.append(f"<agents>\n{self.agents_md.strip()}\n</agents>")
        if self.tools_md:
            sections.append(f"<tools>\n{self.tools_md.strip()}\n</tools>")
        return "\n\n".join(sections)


def _read_if_exists(root: Path, name: str) -> tuple[str | None, str | None]:
    path = root / name
    if path.is_file():
        return path.read_text(encoding="utf-8"), str(path)
    return None, None


def load_injection_bundle(root: str | Path) -> InjectionBundle:
    """Load AGENTS / SOUL / TOOLS markdown from ``root``."""
    base = Path(root)
    agents, agents_src = _read_if_exists(base, "AGENTS.md")
    soul, soul_src = _read_if_exists(base, "SOUL.md")
    tools, tools_src = _read_if_exists(base, "TOOLS.md")
    sources: dict[str, Any] = {}
    if agents_src:
        sources["AGENTS.md"] = agents_src
    if soul_src:
        sources["SOUL.md"] = soul_src
    if tools_src:
        sources["TOOLS.md"] = tools_src
    return InjectionBundle(
        agents_md=agents,
        soul_md=soul,
        tools_md=tools,
        sources=sources,
    )


__all__ = ["InjectionBundle", "load_injection_bundle"]
