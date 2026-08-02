"""Unit tests for the Baselithbot plugin — ClawHub HTTP client."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


def test_clawhub_client_default_config() -> None:
    from plugins.baselithbot.skills import DEFAULT_HUB_URL, ClawHubClient, ClawHubConfig

    client = ClawHubClient()
    assert client.config.base_url == DEFAULT_HUB_URL

    custom = ClawHubClient(ClawHubConfig(base_url="https://example.org/hub"))
    assert custom.config.base_url == "https://example.org/hub"


class _FakeClawHubResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


class _FakeClawHubAsyncClient:
    def __init__(
        self,
        responses: dict[tuple[str, tuple[tuple[str, str], ...]], _FakeClawHubResponse],
        **_: Any,
    ) -> None:
        self._responses = responses

    async def __aenter__(self) -> _FakeClawHubAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ):
        key = (
            url,
            tuple(sorted((str(k), str(v)) for k, v in (params or {}).items())),
        )
        return self._responses.get(
            key, _FakeClawHubResponse(status_code=404, json_data={"missing": url})
        )


@pytest.mark.asyncio
async def test_clawhub_install_accepts_bundle_without_manifest_as_provisional(
    tmp_path,
) -> None:
    import httpx

    from plugins.baselithbot.skills import ClawHubClient, ClawHubConfig

    responses = {
        (
            "https://clawhub.ai/api/v1/skills/demo",
            (),
        ): _FakeClawHubResponse(
            json_data={
                "skill": {"displayName": "Demo Skill", "summary": "demo"},
                "latestVersion": {"version": "1.0.0"},
            }
        ),
        (
            "https://clawhub.ai/api/v1/skills/demo/file",
            (("path", "SKILL.md"),),
        ): _FakeClawHubResponse(
            text="---\nname: Demo Skill\ndescription: demo\n---\n\n# Demo\n"
        ),
        (
            "https://clawhub.ai/api/v1/skills/demo/file",
            (("path", "MANIFEST.yaml"),),
        ): _FakeClawHubResponse(status_code=404, text=""),
    }

    with patch.object(
        httpx,
        "AsyncClient",
        side_effect=lambda **kwargs: _FakeClawHubAsyncClient(responses, **kwargs),
    ):
        client = ClawHubClient(ClawHubConfig(install_dir=str(tmp_path)))
        result = await client.install("demo")

    assert result["status"] == "success"
    compat = result["compatibility"]
    assert compat["compatible"] is True
    assert compat["status"] == "provisional"
    assert "compatibility section" in " ".join(compat.get("warnings", []))


@pytest.mark.asyncio
async def test_clawhub_install_materializes_verified_bundle(tmp_path) -> None:
    import httpx

    from plugins.baselithbot.skills import ClawHubClient, ClawHubConfig, SkillRegistry

    manifest_text = """
bundle: demo-skill
bundle_version: 1.2.3
description: Verified demo skill
compatibility:
  designed_for:
    surfaces:
      - cli
      - chat
  tested_on:
    - platform: OpenClaw
      model: GPT-5
      surface: cli
      status: pass
      date: 2026-04-17
""".strip()
    skill_text = "---\nname: Demo Skill\ndescription: verified demo\n---\n\n# Demo\n"

    responses = {
        (
            "https://clawhub.ai/api/v1/skills/demo",
            (),
        ): _FakeClawHubResponse(
            json_data={
                "skill": {
                    "displayName": "Demo Skill",
                    "summary": "verified demo",
                },
                "latestVersion": {"version": "1.2.3"},
            }
        ),
        (
            "https://clawhub.ai/api/v1/skills/demo/file",
            (("path", "SKILL.md"),),
        ): _FakeClawHubResponse(text=skill_text),
        (
            "https://clawhub.ai/api/v1/skills/demo/file",
            (("path", "MANIFEST.yaml"),),
        ): _FakeClawHubResponse(text=manifest_text),
    }

    with patch.object(
        httpx,
        "AsyncClient",
        side_effect=lambda **kwargs: _FakeClawHubAsyncClient(responses, **kwargs),
    ):
        client = ClawHubClient(ClawHubConfig(install_dir=str(tmp_path)))
        registry = SkillRegistry()
        result = await client.install("demo", registry=registry)

    assert result["status"] == "success"
    installed = registry.get("demo")
    assert installed is not None
    assert installed.entrypoint is not None
    installed_dir = tmp_path / "demo"
    assert installed_dir.is_dir()
    assert (installed_dir / "SKILL.md").read_text() == skill_text
    assert (installed_dir / "MANIFEST.yaml").read_text() == manifest_text
    assert installed.metadata["compatibility"]["compatible"] is True
    assert installed.metadata["compatibility"]["status"] == "verified"
