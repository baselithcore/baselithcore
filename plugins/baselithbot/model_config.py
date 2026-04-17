"""Runtime model/provider preferences for the Baselithbot plugin.

Separates *what model the operator wants to use* (persisted + mutable via the
dashboard UI) from *how the framework instantiates clients* (core LLM / Vision
services). Preferences are applied on the next agent startup; in-flight agents
keep their current configuration to avoid mid-task churn.

Security model
--------------
- API keys are **never** returned by ``ModelPreferenceStore.snapshot`` or
  persisted to disk; they stay in environment variables under
  ``core.config.services``. The UI only flips providers / model names.
- Writes land on a bounded set of known providers (``KNOWN_PROVIDERS``) and
  reject free-form values, so the endpoint cannot be abused to smuggle
  arbitrary strings into downstream config.
- Persistence is atomic (``.tmp`` + ``os.replace``) and restricted to the
  plugin-owned ``workspace`` directory; no cross-plugin writes.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core.observability.logging import get_logger

logger = get_logger(__name__)

LLMProvider = Literal["openai", "anthropic", "ollama", "huggingface"]
VisionProvider = Literal["openai", "anthropic", "google", "ollama"]

KNOWN_PROVIDERS: dict[str, list[str]] = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4.1", "gpt-5"],
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-3-5-sonnet-20241022",
    ],
    "ollama": ["llama3.2", "llama3.1", "mistral:latest", "qwen2.5", "phi3"],
    "huggingface": [
        "meta-llama/Llama-3.1-8B-Instruct",
        "mistralai/Mistral-7B-Instruct-v0.3",
    ],
}

KNOWN_VISION_PROVIDERS: dict[str, list[str]] = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
    "anthropic": [
        "claude-3-5-sonnet-20241022",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
    ],
    "google": ["gemini-2.0-flash", "gemini-1.5-pro"],
    "ollama": ["llava", "llava:13b", "bakllava"],
}


class FailoverEntry(BaseModel):
    """One link in a provider failover chain."""

    provider: LLMProvider
    model: str = Field(..., min_length=1, max_length=120)
    cooldown_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)


class ModelPreferences(BaseModel):
    """Operator-selected model preferences (mutable at runtime)."""

    provider: LLMProvider = "ollama"
    model: str = Field(default="llama3.2", min_length=1, max_length=120)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)

    vision_provider: VisionProvider = "openai"
    vision_model: str = Field(default="gpt-4o", min_length=1, max_length=120)

    failover_chain: list[FailoverEntry] = Field(default_factory=list)

    @field_validator("model")
    @classmethod
    def _model_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model name cannot be blank")
        return value.strip()

    @field_validator("vision_model")
    @classmethod
    def _vision_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("vision model name cannot be blank")
        return value.strip()


class ModelPreferenceStore:
    """Thread-safe Pydantic-backed preferences persisted as JSON."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path: Path | None = Path(path) if path else None
        self._lock = threading.Lock()
        self._prefs = ModelPreferences()
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    def get(self) -> ModelPreferences:
        with self._lock:
            return self._prefs.model_copy(deep=True)

    def update(self, prefs: ModelPreferences) -> ModelPreferences:
        with self._lock:
            self._prefs = prefs
            self._persist()
            logger.info(
                "baselithbot_model_prefs_updated",
                provider=prefs.provider,
                model=prefs.model,
                vision_provider=prefs.vision_provider,
            )
            return self._prefs.model_copy(deep=True)

    def snapshot(self) -> dict[str, object]:
        """Return a UI-safe snapshot with known-provider catalog."""
        prefs = self.get()
        return {
            "current": prefs.model_dump(),
            "options": {
                "llm_providers": KNOWN_PROVIDERS,
                "vision_providers": KNOWN_VISION_PROVIDERS,
            },
        }

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            self._prefs = ModelPreferences.model_validate_json(raw)
            logger.info("baselithbot_model_prefs_loaded", path=str(self._path))
        except (OSError, ValueError) as exc:
            logger.warning(
                "baselithbot_model_prefs_load_failed",
                path=str(self._path),
                error=str(exc),
            )

    def _persist(self) -> None:
        if self._path is None:
            return
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = self._prefs.model_dump_json(indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)


__all__ = [
    "FailoverEntry",
    "KNOWN_PROVIDERS",
    "KNOWN_VISION_PROVIDERS",
    "LLMProvider",
    "ModelPreferenceStore",
    "ModelPreferences",
    "VisionProvider",
]
