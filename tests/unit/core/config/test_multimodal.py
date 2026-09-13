"""Unit tests for ``core.config.multimodal``."""

from __future__ import annotations

from core.config.multimodal import VisionConfig


class TestVisionConfigDefaults:
    def test_anthropic_model_default_is_a_current_model(self, monkeypatch):
        # Isolate from any ANTHROPIC/VISION_ANTHROPIC_MODEL set in the real
        # environment or a developer .env — this is a default-value test.
        for var in ("VISION_ANTHROPIC_MODEL",):
            monkeypatch.delenv(var, raising=False)
        assert VisionConfig().anthropic_model == "claude-opus-5"
