"""Loaders and validators for local baselithbot skill bundles.

Baselithbot skills are compatible with the OpenClaw authoring spec
(https://docs.openclaw.ai/tools/skills) and with ClawHub marketplace
bundles. A bundle is a directory containing:

* ``SKILL.md`` — required. YAML frontmatter + markdown body.
  Frontmatter MUST declare ``name`` and ``description``. It MAY
  additionally declare OpenClaw-native fields: ``homepage``,
  ``user-invocable``, ``disable-model-invocation``, ``command-dispatch``,
  ``command-tool``, ``command-arg-mode``, and a ``metadata.openclaw``
  block (``requires``, ``install``, ``os``, ``always``, ``emoji``,
  ``primaryEnv``, ``skillKey``). ClawHub-style baselithbot extensions
  (``tags``, ``version``) are preserved verbatim in ``frontmatter``.
* ``MANIFEST.yaml`` — optional. Baselithbot/ClawHub quality signal that
  adds ``bundle_version`` and a ``compatibility`` block so the UI can
  surface a verified/provisional/invalid state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_SUPPORTED_SURFACES = {"chat", "cli", "ide"}
_OPENCLAW_OS_VALUES = {"darwin", "linux", "win32"}


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


class OpenClawRequires(BaseModel):
    bins: list[str] = Field(default_factory=list)
    any_bins: list[str] = Field(default_factory=list)
    env: list[str] = Field(default_factory=list)
    config: list[str] = Field(default_factory=list)


class OpenClawSpec(BaseModel):
    """Typed view onto the OpenClaw-native fields of ``SKILL.md`` frontmatter."""

    homepage: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    command_dispatch: str | None = None
    command_tool: str | None = None
    command_arg_mode: str | None = None
    always: bool = False
    emoji: str | None = None
    os: list[str] = Field(default_factory=list)
    primary_env: str | None = None
    skill_key: str | None = None
    requires: OpenClawRequires = Field(default_factory=OpenClawRequires)
    install: list[dict[str, Any]] = Field(default_factory=list)


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
    openclaw: OpenClawSpec | None = None


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


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return []


def _extract_openclaw(
    frontmatter: dict[str, Any],
) -> tuple[OpenClawSpec | None, list[str]]:
    """Return the OpenClaw view of ``frontmatter`` and any validation warnings.

    The frontmatter uses dash-case keys (``user-invocable``) per OpenClaw
    spec; the returned pydantic model normalizes to snake_case.
    """
    warnings: list[str] = []

    def _pop(*keys: str) -> Any:
        for key in keys:
            if key in frontmatter:
                return frontmatter[key]
        return None

    metadata = frontmatter.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = yaml.safe_load(metadata) or {}
        except Exception:
            warnings.append(
                "metadata frontmatter must be a valid inline JSON/YAML object"
            )
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    openclaw_meta = metadata.get("openclaw") if isinstance(metadata, dict) else {}
    if not isinstance(openclaw_meta, dict):
        openclaw_meta = {}

    requires_raw = openclaw_meta.get("requires", {})
    requires = OpenClawRequires()
    if isinstance(requires_raw, dict):
        requires = OpenClawRequires(
            bins=_coerce_str_list(requires_raw.get("bins")),
            any_bins=_coerce_str_list(
                requires_raw.get("anyBins") or requires_raw.get("any_bins")
            ),
            env=_coerce_str_list(requires_raw.get("env")),
            config=_coerce_str_list(requires_raw.get("config")),
        )

    os_values = _coerce_str_list(openclaw_meta.get("os"))
    for value in os_values:
        if value not in _OPENCLAW_OS_VALUES:
            warnings.append(
                f"metadata.openclaw.os entry '{value}' is not in {sorted(_OPENCLAW_OS_VALUES)}"
            )

    install_raw = openclaw_meta.get("install")
    install_entries: list[dict[str, Any]] = []
    if isinstance(install_raw, list):
        for entry in install_raw:
            if isinstance(entry, dict):
                install_entries.append(entry)

    user_invocable = _pop("user-invocable", "user_invocable")
    disable_model = _pop("disable-model-invocation", "disable_model_invocation")
    command_dispatch = _pop("command-dispatch", "command_dispatch")
    command_tool = _pop("command-tool", "command_tool")
    command_arg_mode = _pop("command-arg-mode", "command_arg_mode")

    has_openclaw_signal = bool(
        openclaw_meta
        or command_dispatch
        or command_tool
        or command_arg_mode
        or frontmatter.get("homepage")
        or user_invocable is False
        or disable_model is True
    )

    if command_dispatch and command_dispatch != "tool":
        warnings.append(
            f"command-dispatch '{command_dispatch}' is not recognized (expected 'tool')"
        )
    if command_dispatch == "tool" and not command_tool:
        warnings.append("command-dispatch=tool requires command-tool to be set")

    if not has_openclaw_signal:
        return None, warnings

    return (
        OpenClawSpec(
            homepage=(
                str(frontmatter["homepage"]).strip()
                if isinstance(frontmatter.get("homepage"), str)
                else None
            ),
            user_invocable=(
                bool(user_invocable) if isinstance(user_invocable, bool) else True
            ),
            disable_model_invocation=(
                bool(disable_model) if isinstance(disable_model, bool) else False
            ),
            command_dispatch=(
                str(command_dispatch).strip()
                if isinstance(command_dispatch, str)
                else None
            ),
            command_tool=(
                str(command_tool).strip() if isinstance(command_tool, str) else None
            ),
            command_arg_mode=(
                str(command_arg_mode).strip()
                if isinstance(command_arg_mode, str)
                else None
            ),
            always=bool(openclaw_meta.get("always", False)),
            emoji=(
                str(openclaw_meta["emoji"]).strip()
                if isinstance(openclaw_meta.get("emoji"), str)
                else None
            ),
            os=os_values,
            primary_env=(
                str(openclaw_meta["primaryEnv"]).strip()
                if isinstance(openclaw_meta.get("primaryEnv"), str)
                else None
            ),
            skill_key=(
                str(openclaw_meta["skillKey"]).strip()
                if isinstance(openclaw_meta.get("skillKey"), str)
                else None
            ),
            requires=requires,
            install=install_entries,
        ),
        warnings,
    )


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


_SKILL_LAYOUT_SUBDIRS: tuple[str, ...] = ("skills", ".agents/skills")


def _iter_skill_dirs(base: Path) -> list[Path]:
    """Return skill directories under OpenClaw-compatible layout roots.

    Layouts scanned (relative to ``base``):

    * ``skills/<slug>`` — ClawHub/baselithbot default.
    * ``.agents/skills/<slug>`` — OpenClaw workspace-local override.

    Symlinked directories whose resolved path escapes ``base`` are
    dropped to prevent path traversal through authored skills.
    """
    base_real = base.resolve()
    found: dict[str, Path] = {}
    for subdir in _SKILL_LAYOUT_SUBDIRS:
        root = base / subdir
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_dir():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            try:
                resolved.relative_to(base_real)
            except ValueError:
                continue
            found.setdefault(path.name, path)
    return list(found.values())


def discover_local_skill_specs(root: str | Path) -> list[LocalSkillSpec]:
    """Discover custom local skills in OpenClaw-compatible bundle layouts.

    Scans ``<root>/skills/<slug>`` and ``<root>/.agents/skills/<slug>``
    for ``SKILL.md`` bundles, honoring OpenClaw precedence (the first
    occurrence of a given slug wins) and rejecting symlinks that escape
    ``root``.
    """
    base = Path(root)
    skill_dirs = _iter_skill_dirs(base)
    if not skill_dirs:
        return []

    specs: list[LocalSkillSpec] = []
    for skill_dir in skill_dirs:
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

        openclaw_spec, openclaw_warnings = _extract_openclaw(frontmatter)
        warnings.extend(openclaw_warnings)

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
                openclaw=openclaw_spec,
            )
        )

    return specs


__all__ = [
    "InjectionBundle",
    "LocalSkillSpec",
    "LocalSkillValidation",
    "OpenClawRequires",
    "OpenClawSpec",
    "discover_local_skill_specs",
    "load_injection_bundle",
]
