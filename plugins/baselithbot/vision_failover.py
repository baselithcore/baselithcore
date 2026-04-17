"""Failover-aware VisionService wrapper for Baselithbot.

Wraps :class:`core.services.vision.service.VisionService` so the plugin can:

- Force a specific *primary* model per provider (the stock service hardcodes
  ``DEFAULT_MODELS`` for non-Ollama providers; the dashboard lets operators
  override them).
- Apply operator-chosen temperature / max_tokens to each ``VisionRequest``.
- Retry on a user-defined failover chain, honoring per-entry cooldowns so a
  broken provider is skipped until it has had a chance to recover.

The chain uses ``LLMProvider`` literals coming from the dashboard; entries
whose provider cannot serve vision (currently only ``huggingface``) are
silently skipped with a log.
"""

from __future__ import annotations

import time

from core.observability.logging import get_logger
from core.services.vision.models import VisionProvider, VisionRequest, VisionResponse
from core.services.vision.service import VisionService

from .model_config import FailoverEntry, ModelPreferences

logger = get_logger(__name__)


_VISION_COMPATIBLE = {"openai", "anthropic", "ollama", "google"}


class FailoverVisionService(VisionService):
    """VisionService that applies operator prefs and retries on failover chain."""

    def __init__(
        self,
        prefs: ModelPreferences,
        *,
        openai_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        google_api_key: str | None = None,
    ) -> None:
        super().__init__(
            default_provider=VisionProvider(prefs.vision_provider),
            openai_api_key=openai_api_key,
            anthropic_api_key=anthropic_api_key,
            google_api_key=google_api_key,
        )
        self._prefs = prefs
        self._chain: list[FailoverEntry] = list(prefs.failover_chain)
        self._cooldowns: dict[int, float] = {}
        self._original_defaults: dict[VisionProvider, str] = dict(self.DEFAULT_MODELS)
        self._apply_primary_model()

    def _apply_primary_model(self) -> None:
        """Pin the operator-chosen model for the primary vision provider."""
        provider = VisionProvider(self._prefs.vision_provider)
        self.DEFAULT_MODELS[provider] = self._prefs.vision_model

    def _override_for_entry(self, entry: FailoverEntry) -> VisionProvider | None:
        """Swap ``DEFAULT_MODELS`` to match ``entry``; returns the vision provider."""
        if entry.provider not in _VISION_COMPATIBLE:
            return None
        provider = VisionProvider(entry.provider)
        self.DEFAULT_MODELS[provider] = entry.model
        return provider

    def _restore_defaults(self) -> None:
        """Restore hardcoded defaults after an attempt."""
        self.DEFAULT_MODELS.clear()
        self.DEFAULT_MODELS.update(self._original_defaults)
        self._apply_primary_model()

    def _apply_request_prefs(self, request: VisionRequest) -> None:
        """Override request tuning knobs with operator preferences."""
        if self._prefs.max_tokens is not None:
            request.max_tokens = self._prefs.max_tokens
        request.temperature = self._prefs.temperature

    async def analyze(self, request: VisionRequest) -> VisionResponse:
        self._apply_request_prefs(request)

        try:
            return await super().analyze(request)
        except Exception as primary_exc:
            last_exc: Exception = primary_exc
            now = time.monotonic()
            for idx, entry in enumerate(self._chain):
                ready_at = self._cooldowns.get(idx, 0.0)
                if ready_at > now:
                    logger.info(
                        "baselithbot_failover_skipped_cooldown",
                        entry_index=idx,
                        provider=entry.provider,
                        ready_in=ready_at - now,
                    )
                    continue

                provider = self._override_for_entry(entry)
                if provider is None:
                    logger.info(
                        "baselithbot_failover_skipped_incompatible",
                        entry_index=idx,
                        provider=entry.provider,
                    )
                    continue

                request.provider = provider
                try:
                    response = await super().analyze(request)
                    return response
                except Exception as exc:
                    self._cooldowns[idx] = (
                        time.monotonic() + entry.cooldown_seconds
                    )
                    logger.warning(
                        "baselithbot_failover_attempt_failed",
                        entry_index=idx,
                        provider=entry.provider,
                        model=entry.model,
                        cooldown=entry.cooldown_seconds,
                        error=str(exc),
                    )
                    last_exc = exc
                finally:
                    self._restore_defaults()
            raise last_exc


__all__ = ["FailoverVisionService"]
