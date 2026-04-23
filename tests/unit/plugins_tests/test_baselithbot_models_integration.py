"""Models tab integration tests.

Covers the contract the dashboard UI depends on:
    - ``_apply_model_preferences`` mutates the global vision config + service
      defaults so the next agent run honors the operator's choice.
    - ``FailoverVisionService`` pins the primary model, applies operator
      tuning knobs, and rotates through the failover chain on error with
      cooldown tracking.
    - ``PUT /dash/models`` triggers apply + singleton invalidation.
"""

from __future__ import annotations

import tempfile
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.services.vision.models import (
    ImageContent,
    VisionCapability,
    VisionProvider,
    VisionRequest,
    VisionResponse,
)
from core.services.vision.service import VisionService
from plugins.baselithbot.config.models import FailoverEntry, ModelPreferences
from plugins.baselithbot.plugin import BaselithbotPlugin
from plugins.baselithbot.api.router import create_router
from plugins.baselithbot.browser.vision_failover import FailoverVisionService


@pytest.fixture
def restore_vision_defaults() -> Any:
    """Snapshot + restore VisionService.DEFAULT_MODELS to avoid test bleed."""
    snapshot = dict(VisionService.DEFAULT_MODELS)
    yield
    VisionService.DEFAULT_MODELS.clear()
    VisionService.DEFAULT_MODELS.update(snapshot)


@pytest.fixture
def restore_vision_config() -> Any:
    """Snapshot + restore the module-level VisionConfig singleton."""
    from core.config import services as cfg_mod

    original = cfg_mod._vision_config
    yield
    cfg_mod._vision_config = original


def _fresh_plugin() -> BaselithbotPlugin:
    return BaselithbotPlugin(state_dir=tempfile.mkdtemp(prefix="baselithbot-models-"))


class TestApplyModelPreferences:
    def test_mutates_vision_config_and_default_models(
        self,
        restore_vision_defaults: Any,
        restore_vision_config: Any,
    ) -> None:
        from core.config import services as cfg_mod

        plugin = _fresh_plugin()
        plugin.model_preferences.update(
            ModelPreferences(
                provider="anthropic",
                model="claude-opus-4-7",
                temperature=0.3,
                max_tokens=2048,
                vision_provider="anthropic",
                vision_model="claude-3-5-sonnet-20241022",
            )
        )
        plugin._apply_model_preferences()

        assert cfg_mod._vision_config is not None
        assert cfg_mod._vision_config.provider == "anthropic"
        assert (
            VisionService.DEFAULT_MODELS[VisionProvider.ANTHROPIC]
            == "claude-3-5-sonnet-20241022"
        )

    def test_ollama_writes_model_into_vision_config(
        self,
        restore_vision_defaults: Any,
        restore_vision_config: Any,
    ) -> None:
        from core.config import services as cfg_mod

        plugin = _fresh_plugin()
        plugin.model_preferences.update(
            ModelPreferences(
                provider="ollama",
                model="llama3.2",
                vision_provider="ollama",
                vision_model="llava:13b",
            )
        )
        plugin._apply_model_preferences()

        assert cfg_mod._vision_config is not None
        assert cfg_mod._vision_config.ollama_model == "llava:13b"


class TestFailoverVisionService:
    def _make_request(self) -> VisionRequest:
        return VisionRequest(
            prompt="analyze",
            images=[
                ImageContent(source_type="base64", data="AAA=", media_type="image/png")
            ],
            capability=VisionCapability.SCREENSHOT_ANALYSIS,
            json_mode=True,
        )

    def test_primary_model_pinned_on_default_models(
        self, restore_vision_defaults: Any
    ) -> None:
        prefs = ModelPreferences(
            vision_provider="anthropic",
            vision_model="claude-custom",
        )
        FailoverVisionService(prefs)
        assert VisionService.DEFAULT_MODELS[VisionProvider.ANTHROPIC] == "claude-custom"

    def test_operator_tuning_applied_to_request(
        self, restore_vision_defaults: Any
    ) -> None:
        prefs = ModelPreferences(temperature=0.42, max_tokens=777)
        svc = FailoverVisionService(prefs)
        req = self._make_request()

        async def fake_analyze(request: VisionRequest) -> VisionResponse:
            assert request.temperature == 0.42
            assert request.max_tokens == 777
            return VisionResponse(
                success=True, content="{}", provider="ollama", model="llama"
            )

        with patch.object(
            VisionService, "analyze", AsyncMock(side_effect=fake_analyze)
        ):
            import asyncio

            asyncio.run(svc.analyze(req))

    def test_failover_rotates_on_primary_error(
        self, restore_vision_defaults: Any
    ) -> None:
        prefs = ModelPreferences(
            vision_provider="openai",
            vision_model="gpt-4o",
            failover_chain=[
                FailoverEntry(
                    provider="anthropic", model="claude-fallback", cooldown_seconds=60.0
                )
            ],
        )
        svc = FailoverVisionService(prefs)
        req = self._make_request()

        calls: list[str] = []

        async def fake_analyze(request: VisionRequest) -> VisionResponse:
            provider = request.provider or svc.default_provider
            calls.append(provider.value)
            if provider == VisionProvider.OPENAI:
                raise RuntimeError("primary down")
            return VisionResponse(
                success=True,
                content="{}",
                provider=provider.value,
                model="claude-fallback",
            )

        with patch.object(
            VisionService, "analyze", AsyncMock(side_effect=fake_analyze)
        ):
            import asyncio

            response = asyncio.run(svc.analyze(req))

        assert calls == ["openai", "anthropic"]
        assert response.provider == "anthropic"
        assert response.model == "claude-fallback"

    def test_huggingface_entry_skipped_as_incompatible(
        self, restore_vision_defaults: Any
    ) -> None:
        prefs = ModelPreferences(
            vision_provider="openai",
            failover_chain=[
                FailoverEntry(
                    provider="huggingface", model="hf-model", cooldown_seconds=0.0
                )
            ],
        )
        svc = FailoverVisionService(prefs)
        req = self._make_request()

        async def always_fail(request: VisionRequest) -> VisionResponse:
            raise RuntimeError("down")

        with patch.object(VisionService, "analyze", AsyncMock(side_effect=always_fail)):
            import asyncio

            with pytest.raises(RuntimeError, match="down"):
                asyncio.run(svc.analyze(req))

    def test_cooldown_prevents_retry(self, restore_vision_defaults: Any) -> None:
        prefs = ModelPreferences(
            vision_provider="openai",
            failover_chain=[
                FailoverEntry(
                    provider="anthropic", model="claude-x", cooldown_seconds=3600.0
                )
            ],
        )
        svc = FailoverVisionService(prefs)
        req = self._make_request()

        async def always_fail(request: VisionRequest) -> VisionResponse:
            raise RuntimeError("down")

        mock = AsyncMock(side_effect=always_fail)
        with patch.object(VisionService, "analyze", mock):
            import asyncio

            with pytest.raises(RuntimeError):
                asyncio.run(svc.analyze(req))
            with pytest.raises(RuntimeError):
                asyncio.run(svc.analyze(req))

        # 1 primary + 1 fallback (round 1); 1 primary only (round 2 — fallback cooling).
        assert mock.call_count == 3


class TestModelsRoute:
    def _build(self) -> tuple[FastAPI, BaselithbotPlugin]:
        plugin = _fresh_plugin()
        app = FastAPI()
        app.include_router(create_router(plugin), prefix="/baselithbot")
        return app, plugin

    def test_put_applies_prefs_and_invalidates_agent(
        self,
        restore_vision_defaults: Any,
        restore_vision_config: Any,
    ) -> None:
        app, plugin = self._build()
        client = TestClient(app)

        # Seed a bogus "agent" so invalidation has something to drop.
        fake_agent = AsyncMock()
        plugin._agent = fake_agent  # type: ignore[assignment]

        res = client.put(
            "/baselithbot/dash/models",
            json={
                "provider": "openai",
                "model": "gpt-4o",
                "temperature": 0.1,
                "max_tokens": 1024,
                "vision_provider": "openai",
                "vision_model": "gpt-4o-mini",
                "failover_chain": [],
            },
        )
        assert res.status_code == 200
        assert plugin._agent is None
        fake_agent.shutdown.assert_awaited_once()
        assert VisionService.DEFAULT_MODELS[VisionProvider.OPENAI] == "gpt-4o-mini"
