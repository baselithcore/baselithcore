"""ClawHub HTTP client.

Targets the public ClawHub API on ``clawhub.ai`` and installs only
bundles that expose explicit compatibility metadata. The client fails
closed when it cannot verify the remote skill bundle structure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from core.observability.logging import get_logger

from .registry import Skill, SkillRegistry, SkillScope

logger = get_logger(__name__)

DEFAULT_HUB_URL = "https://clawhub.ai/api/v1"
DEFAULT_CONVEX_URL = "https://wry-manatee-359.convex.cloud"
_SUPPORTED_SURFACES = {"chat", "cli", "ide"}


class ClawHubConfig(BaseModel):
    base_url: str = Field(default=DEFAULT_HUB_URL)
    convex_url: str = Field(default=DEFAULT_CONVEX_URL)
    install_dir: str = Field(default="./skills")
    auth_token: str | None = None
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=300.0)


class ClawHubClient:
    """Fetch / vet / install skills against the official ClawHub API."""

    def __init__(self, config: ClawHubConfig | None = None) -> None:
        self._config = config or ClawHubConfig()

    @property
    def config(self) -> ClawHubConfig:
        return self._config

    def _headers(self, *, accept: str = "application/json") -> dict[str, str]:
        headers = {"Accept": accept}
        if self._config.auth_token:
            headers["Authorization"] = f"Bearer {self._config.auth_token}"
        return headers

    def _api_url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _normalize_identifier(self, entry: dict[str, Any]) -> str | None:
        direct = entry.get("identifier") or entry.get("name")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        slug = entry.get("slug")
        if isinstance(slug, str) and slug.strip():
            slug = slug.strip()
            owner = (
                entry.get("owner")
                or entry.get("author")
                or entry.get("publisher")
                or entry.get("namespace")
            )
            if isinstance(owner, dict):
                owner = (
                    owner.get("username") or owner.get("handle") or owner.get("name")
                )
            if isinstance(owner, str) and owner.strip() and "/" not in slug:
                return f"{owner.strip()}/{slug}"
            return slug
        return None

    def _normalize_catalog_entry(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        identifier = self._normalize_identifier(entry)
        if not identifier:
            return None
        version = entry.get("version") or entry.get("currentVersion") or "0.0.0"
        description = entry.get("summary") or entry.get("description") or ""
        return {
            "name": identifier,
            "version": str(version),
            "description": str(description),
            "metadata": {"source": "clawhub", "catalog_entry": entry},
        }

    def _split_identifier(self, identifier: str) -> tuple[str, str] | None:
        if "/" not in identifier:
            return None
        owner, slug = identifier.split("/", 1)
        owner = owner.strip()
        slug = slug.strip()
        if not owner or not slug:
            return None
        return owner, slug

    def _extract_frontmatter(self, text: str) -> dict[str, Any]:
        if not text.startswith("---\n"):
            return {}
        _, _, remainder = text.partition("---\n")
        frontmatter, sep, _ = remainder.partition("\n---")
        if not sep:
            return {}
        parsed = yaml.safe_load(frontmatter) or {}
        return parsed if isinstance(parsed, dict) else {}

    async def _request_json(
        self, client: Any, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        resp = await client.get(
            self._api_url(path), headers=self._headers(), params=params
        )
        if not resp.is_success:
            return {"status": "error", "http_status": resp.status_code, "path": path}
        try:
            return resp.json()
        except Exception as exc:
            return {"status": "error", "error": str(exc), "path": path}

    async def _request_text(
        self, client: Any, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        resp = await client.get(
            self._api_url(path),
            headers=self._headers(accept="text/plain"),
            params=params,
        )
        if not resp.is_success:
            return {"status": "error", "http_status": resp.status_code, "path": path}
        return {"status": "success", "content": resp.text}

    def _convex_query_url(self) -> str:
        return f"{self._config.convex_url.rstrip('/')}/api/query"

    def _flatten_convex_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Normalize Convex ``skills:list`` items to the REST catalog shape.

        ``skills:list`` only exposes the internal ``latestVersionId`` pointer
        and never the semver string, so we deliberately leave ``version``
        unset; ``_normalize_catalog_entry`` will default it to ``"0.0.0"``
        until the installer resolves the real manifest.
        """
        flat = dict(entry)
        if entry.get("summary") and not flat.get("description"):
            flat["description"] = str(entry.get("summary"))
        flat.pop("version", None)
        return flat

    async def _fetch_convex_skills(self, client: Any) -> list[dict[str, Any]]:
        """Call the Convex ``skills:list`` query and return the raw items list."""
        url = self._convex_query_url()
        payload = {"path": "skills:list", "args": {}, "format": "json"}
        try:
            resp = await client.post(
                url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    **(
                        {"Authorization": f"Bearer {self._config.auth_token}"}
                        if self._config.auth_token
                        else {}
                    ),
                },
                json=payload,
            )
        except Exception as exc:
            logger.warning("baselithbot_clawhub_convex_error", error=str(exc))
            return []
        if not resp.is_success:
            return []
        try:
            body = resp.json()
        except Exception:
            return []
        if isinstance(body, dict) and body.get("status") == "success":
            value = body.get("value")
            if isinstance(value, list):
                return [entry for entry in value if isinstance(entry, dict)]
        return []

    async def list_skills(self) -> list[dict[str, Any]]:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return [{"status": "error", "error": "httpx not installed"}]

        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            raw = await self._request_json(
                client, "/skills", params={"sort": "trending", "limit": 100}
            )

            rest_items: list[dict[str, Any]] = []
            rest_error: dict[str, Any] | None = None
            if isinstance(raw, dict) and raw.get("status") == "error":
                rest_error = raw
            else:
                candidates = raw.get("items") if isinstance(raw, dict) else raw
                if isinstance(candidates, list):
                    rest_items = [
                        entry for entry in candidates if isinstance(entry, dict)
                    ]

            source_items = rest_items
            if not source_items:
                source_items = await self._fetch_convex_skills(client)

        if not source_items and rest_error is not None:
            return [rest_error]

        normalized: list[dict[str, Any]] = []
        for entry in source_items:
            flat = self._flatten_convex_entry(entry)
            candidate = self._normalize_catalog_entry(flat)
            if candidate is not None:
                normalized.append(candidate)
        return normalized

    async def get_manifest(self, identifier: str) -> dict[str, Any]:
        split = self._split_identifier(identifier)
        if split is None:
            return {
                "status": "error",
                "error": "ClawHub skill identifier must be in owner/slug format",
            }

        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        owner, slug = split

        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            detail = await self._request_json(client, f"/skills/{owner}/{slug}")
            skill_md = await self._request_text(
                client, f"/skills/{owner}/{slug}/file", params={"path": "SKILL.md"}
            )
            manifest_yaml = await self._request_text(
                client, f"/skills/{owner}/{slug}/file", params={"path": "MANIFEST.yaml"}
            )

        if isinstance(skill_md, dict) and skill_md.get("status") == "error":
            return skill_md

        if not isinstance(skill_md, dict) or "content" not in skill_md:
            return {"status": "error", "error": "missing SKILL.md from ClawHub bundle"}

        skill_text = str(skill_md["content"])
        frontmatter = self._extract_frontmatter(skill_text)

        parsed_manifest: dict[str, Any] = {}
        if isinstance(manifest_yaml, dict) and manifest_yaml.get("status") == "success":
            try:
                loaded = yaml.safe_load(str(manifest_yaml["content"])) or {}
                if isinstance(loaded, dict):
                    parsed_manifest = loaded
            except Exception as exc:
                return {"status": "error", "error": f"invalid MANIFEST.yaml: {exc}"}

        name = (
            parsed_manifest.get("bundle")
            or frontmatter.get("name")
            or (detail.get("displayName") if isinstance(detail, dict) else None)
            or identifier
        )
        version = (
            parsed_manifest.get("bundle_version")
            or frontmatter.get("version")
            or (detail.get("version") if isinstance(detail, dict) else None)
            or (detail.get("currentVersion") if isinstance(detail, dict) else None)
            or "0.0.0"
        )
        description = (
            parsed_manifest.get("description")
            or frontmatter.get("description")
            or (detail.get("summary") if isinstance(detail, dict) else None)
            or (detail.get("description") if isinstance(detail, dict) else None)
            or ""
        )

        return {
            "status": "success",
            "identifier": identifier,
            "name": str(name),
            "version": str(version),
            "description": str(description),
            "compatibility": parsed_manifest.get("compatibility"),
            "detail": detail if isinstance(detail, dict) else {},
            "remote_files": {
                "SKILL.md": skill_text,
                **(
                    {"MANIFEST.yaml": str(manifest_yaml["content"])}
                    if isinstance(manifest_yaml, dict)
                    and manifest_yaml.get("status") == "success"
                    else {}
                ),
            },
            "manifest": parsed_manifest,
        }

    def _evaluate_compatibility(self, manifest: dict[str, Any]) -> dict[str, Any]:
        compatibility = manifest.get("compatibility")
        errors: list[str] = []

        if not isinstance(compatibility, dict):
            errors.append("MANIFEST.yaml is missing the compatibility section")
            return {
                "compatible": False,
                "errors": errors,
                "surfaces": [],
                "tested_on": [],
            }

        designed_for = compatibility.get("designed_for")
        surfaces: list[str] = []
        if isinstance(designed_for, dict):
            raw_surfaces = designed_for.get("surfaces")
            if isinstance(raw_surfaces, list):
                surfaces = [
                    str(surface).strip().lower() for surface in raw_surfaces if surface
                ]

        if not surfaces:
            errors.append("compatibility.designed_for.surfaces is missing or empty")
        elif not any(surface in _SUPPORTED_SURFACES for surface in surfaces):
            errors.append(
                "compatibility.designed_for.surfaces does not declare a supported agent surface"
            )

        tested_on = compatibility.get("tested_on")
        passing_tests: list[dict[str, str]] = []
        if isinstance(tested_on, list):
            for entry in tested_on:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("status", "")).strip().lower() != "pass":
                    continue
                passing_tests.append(
                    {
                        "platform": str(entry.get("platform", "")),
                        "model": str(entry.get("model", "")),
                        "surface": str(entry.get("surface", "")),
                        "date": str(entry.get("date", "")),
                    }
                )

        if not passing_tests:
            errors.append(
                "compatibility.tested_on does not include any passing validation entry"
            )

        return {
            "compatible": not errors,
            "errors": errors,
            "surfaces": sorted(set(filter(None, surfaces))),
            "tested_on": passing_tests,
        }

    async def install(
        self, identifier: str, registry: SkillRegistry | None = None
    ) -> dict[str, Any]:
        manifest = await self.get_manifest(identifier)
        if manifest.get("status") == "error":
            return manifest

        compatibility = self._evaluate_compatibility(manifest.get("manifest") or {})
        if not compatibility["compatible"]:
            return {
                "status": "error",
                "error": "compatibility validation failed",
                "compatibility": compatibility,
            }

        remote_files = manifest.get("remote_files") or {}
        skill_md = remote_files.get("SKILL.md")
        if not isinstance(skill_md, str) or not skill_md.strip():
            return {
                "status": "error",
                "error": "downloaded bundle does not contain SKILL.md",
            }

        install_root = Path(self._config.install_dir).expanduser().resolve()
        install_root.mkdir(parents=True, exist_ok=True)
        directory_name = identifier.replace("/", "__")
        skill_dir = install_root / directory_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        total_bytes = 0
        for filename, content in remote_files.items():
            target = skill_dir / filename
            target.write_text(content, encoding="utf-8")
            total_bytes += len(content.encode("utf-8"))

        if registry is not None:
            registry.register(
                Skill(
                    name=identifier,
                    version=str(manifest.get("version", "0.0.0")),
                    scope=SkillScope.MANAGED,
                    description=str(manifest.get("description", "")),
                    entrypoint=str(skill_dir),
                    metadata={
                        "source": "clawhub",
                        "official_base_url": self._config.base_url,
                        "compatibility": compatibility,
                        "manifest": manifest.get("manifest") or {},
                        "detail": manifest.get("detail") or {},
                    },
                )
            )

        logger.info(
            "baselithbot_clawhub_installed",
            identifier=identifier,
            bytes=total_bytes,
            install_dir=str(skill_dir),
        )
        return {
            "status": "success",
            "name": identifier,
            "bytes": total_bytes,
            "path": str(skill_dir),
            "compatibility": compatibility,
        }

    async def sync(self, registry: SkillRegistry) -> dict[str, Any]:
        listings = await self.list_skills()
        installed = 0
        errors: list[dict[str, Any]] = []
        for entry in listings:
            name = entry.get("name") if isinstance(entry, dict) else None
            if not name:
                continue
            result = await self.install(name, registry=registry)
            if result.get("status") == "success":
                installed += 1
            else:
                errors.append({"name": name, "error": result})
        return {"status": "success", "installed": installed, "errors": errors}


__all__ = ["ClawHubClient", "ClawHubConfig", "DEFAULT_HUB_URL"]
