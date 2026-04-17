"""Loaders and validators for local baselithbot skill bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_SUPPORTED_SURFACES = {"chat", "cli", "ide"}


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


class LocalSkillValidation(BaseModel):
    status: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    surfaces: list[str] = Field(default_factory=list)
    tested_on: list[dict[str, str]] = Field(default_factory=list)


class LocalSkillSpec(BaseModel):
    slug: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    entrypoint: str
    files: dict[str, str] = Field(default_factory=dict)
    validation: LocalSkillValidation
    manifest: dict[str, Any] = Field(default_factory=dict)
    frontmatter: dict[str, Any] = Field(default_factory=dict)


def _read_if_exists(root: Path, name: str) -> tuple[str | None, str | None]:
    path = root / name
    if path.is_file():
        return path.read_text(encoding="utf-8"), str(path)
    return None, None


def _extract_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    _, _, remainder = text.partition("---\n")
    frontmatter, sep, _ = remainder.partition("\n---")
    if not sep:
        return {}
    parsed = yaml.safe_load(frontmatter) or {}
    return parsed if isinstance(parsed, dict) else {}


def _evaluate_compatibility(
    manifest: dict[str, Any],
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    compatibility = manifest.get("compatibility")
    warnings: list[str] = []
    surfaces: list[str] = []
    tested_on: list[dict[str, str]] = []

    if not isinstance(compatibility, dict):
        warnings.append("MANIFEST.yaml is missing the compatibility section")
        return surfaces, warnings, tested_on

    designed_for = compatibility.get("designed_for")
    if isinstance(designed_for, dict):
        raw_surfaces = designed_for.get("surfaces")
        if isinstance(raw_surfaces, list):
            surfaces = [
                str(surface).strip().lower()
                for surface in raw_surfaces
                if str(surface).strip()
            ]

    if not surfaces:
        warnings.append("compatibility.designed_for.surfaces is missing or empty")
    elif not any(surface in _SUPPORTED_SURFACES for surface in surfaces):
        warnings.append("compatibility does not declare a supported surface")

    raw_tested_on = compatibility.get("tested_on")
    if isinstance(raw_tested_on, list):
        for entry in raw_tested_on:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("status", "")).strip().lower() != "pass":
                continue
            tested_on.append(
                {
                    "platform": str(entry.get("platform", "")),
                    "model": str(entry.get("model", "")),
                    "surface": str(entry.get("surface", "")),
                    "date": str(entry.get("date", "")),
                }
            )

    if not tested_on:
        warnings.append(
            "compatibility.tested_on does not include a passing validation entry"
        )

    return sorted(set(surfaces)), warnings, tested_on


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


def discover_local_skill_specs(root: str | Path) -> list[LocalSkillSpec]:
    """Discover custom local skills in ``root/skills/<slug>/SKILL.md`` format."""
    base = Path(root)
    skills_root = base / "skills"
    if not skills_root.is_dir():
        return []

    specs: list[LocalSkillSpec] = []
    for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        files: dict[str, str] = {}
        errors: list[str] = []
        warnings: list[str] = []

        skill_md_path = skill_dir / "SKILL.md"
        if skill_md_path.is_file():
            skill_text = skill_md_path.read_text(encoding="utf-8")
            files["SKILL.md"] = str(skill_md_path)
            frontmatter = _extract_frontmatter(skill_text)
        else:
            skill_text = ""
            frontmatter = {}
            errors.append("SKILL.md is missing")

        manifest_path = skill_dir / "MANIFEST.yaml"
        manifest: dict[str, Any] = {}
        if manifest_path.is_file():
            files["MANIFEST.yaml"] = str(manifest_path)
            try:
                parsed_manifest = (
                    yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
                )
                if isinstance(parsed_manifest, dict):
                    manifest = parsed_manifest
                else:
                    errors.append("MANIFEST.yaml does not contain a YAML object")
            except Exception as exc:
                errors.append(f"MANIFEST.yaml is invalid: {exc}")

        name = str(frontmatter.get("name") or skill_dir.name).strip()
        if not name:
            errors.append("skill frontmatter must declare a non-empty name")

        description = str(frontmatter.get("description") or "").strip()
        if not description:
            errors.append("skill frontmatter must declare a description")

        surfaces, compatibility_warnings, tested_on = _evaluate_compatibility(manifest)
        warnings.extend(compatibility_warnings)

        if errors:
            status = "invalid"
        elif warnings:
            status = "provisional"
        else:
            status = "verified"

        specs.append(
            LocalSkillSpec(
                slug=skill_dir.name,
                name=name,
                version=str(
                    manifest.get("bundle_version")
                    or frontmatter.get("version")
                    or "0.0.0"
                ),
                description=description,
                entrypoint=str(skill_dir),
                files=files,
                validation=LocalSkillValidation(
                    status=status,
                    errors=errors,
                    warnings=warnings,
                    surfaces=surfaces,
                    tested_on=tested_on,
                ),
                manifest=manifest,
                frontmatter=frontmatter,
            )
        )

    return specs


__all__ = [
    "InjectionBundle",
    "LocalSkillSpec",
    "LocalSkillValidation",
    "discover_local_skill_specs",
    "load_injection_bundle",
]
