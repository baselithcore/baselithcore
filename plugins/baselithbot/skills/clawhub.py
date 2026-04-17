"""ClawHub HTTP client.

Targets a configurable remote registry that exposes:
    - ``GET  /skills`` -> JSON list of available skills
    - ``GET  /skills/{name}`` -> full manifest
    - ``GET  /skills/{name}/download`` -> raw artifact (markdown/zip)

The client is purely declarative: install / sync only fetch + persist
under a configurable ``install_dir``. Plugin lifecycle decides whether
to register the result with ``SkillRegistry``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from core.observability.logging import get_logger

from .registry import Skill, SkillRegistry, SkillScope

logger = get_logger(__name__)

DEFAULT_HUB_URL = "https://clawhub.baselithcore.xyz"


class ClawHubConfig(BaseModel):
    base_url: str = Field(default=DEFAULT_HUB_URL)
    install_dir: str = Field(default="./skills")
    auth_token: str | None = None
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=300.0)


class ClawHubClient:
    """Fetch / install / sync skills against a remote ClawHub registry."""

    def __init__(self, config: ClawHubConfig | None = None) -> None:
        self._config = config or ClawHubConfig()

    @property
    def config(self) -> ClawHubConfig:
        return self._config

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._config.auth_token:
            h["Authorization"] = f"Bearer {self._config.auth_token}"
        return h

    async def list_skills(self) -> list[dict[str, Any]]:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return [{"status": "error", "error": "httpx not installed"}]

        url = f"{self._config.base_url.rstrip('/')}/skills"
        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            resp = await client.get(url, headers=self._headers())
        if not resp.is_success:
            return [{"status": "error", "http_status": resp.status_code}]
        try:
            return list(resp.json())
        except Exception as exc:
            return [{"status": "error", "error": str(exc)}]

    async def get_manifest(self, name: str) -> dict[str, Any]:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        url = f"{self._config.base_url.rstrip('/')}/skills/{name}"
        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            resp = await client.get(url, headers=self._headers())
        if not resp.is_success:
            return {"status": "error", "http_status": resp.status_code}
        try:
            return dict(resp.json())
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    async def install(
        self, name: str, registry: SkillRegistry | None = None
    ) -> dict[str, Any]:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        manifest = await self.get_manifest(name)
        if manifest.get("status") == "error":
            return manifest

        url = f"{self._config.base_url.rstrip('/')}/skills/{name}/download"
        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
            resp = await client.get(url, headers=self._headers())
        if not resp.is_success:
            return {
                "status": "error",
                "http_status": resp.status_code,
                "stage": "download",
            }

        install_root = Path(self._config.install_dir).expanduser().resolve()
        install_root.mkdir(parents=True, exist_ok=True)
        artifact_path = install_root / f"{name}.bin"
        artifact_path.write_bytes(resp.content)

        if registry is not None:
            registry.register(
                Skill(
                    name=manifest.get("name", name),
                    version=manifest.get("version", "0.0.0"),
                    scope=SkillScope.MANAGED,
                    description=manifest.get("description", ""),
                    entrypoint=str(artifact_path),
                    metadata={"source": "clawhub", "manifest": manifest},
                )
            )
        logger.info(
            "baselithbot_clawhub_installed",
            name=name,
            bytes=len(resp.content),
            install_dir=str(install_root),
        )
        return {
            "status": "success",
            "name": name,
            "bytes": len(resp.content),
            "path": str(artifact_path),
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
