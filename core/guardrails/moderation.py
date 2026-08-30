"""Content moderation providers — the consumer for ``moderation_enabled``.

``GuardrailsConfig.moderation_enabled`` / ``moderation_threshold`` existed as
declared configuration with no consumer. This module gives them one: a
pluggable moderator invoked from the orchestrator guard pipeline
(:func:`core.orchestration.guard_pipeline.guard_input_async`).

Activation is deliberate, not implicit: a provider must be named via
``BASELITH_MODERATION_PROVIDER`` (currently ``openai`` — the OpenAI moderation
API, free of charge). Merely having an OpenAI key configured does NOT start
moderating traffic — that would silently add a network call to every request.

Failure policy is **fail-open**: a moderation-endpoint outage degrades to
unmoderated service with a warning, never to a chat outage. The regex-based
``InputGuard`` still runs first and is unaffected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from core.guardrails.config import GuardrailsConfig
from core.observability.logging import get_logger

logger = get_logger(__name__)

_ENV_PROVIDER = "BASELITH_MODERATION_PROVIDER"
_DEFAULT_OPENAI_MODEL = "omni-moderation-latest"
#: Moderation APIs cap input size; truncating keeps one oversized message from
#: erroring the call (the regex guard already caps overall input length).
_MAX_MODERATION_CHARS = 8192


def get_guardrails_config() -> GuardrailsConfig:
    """Guardrails config used by moderation (seam for tests/overrides)."""
    return GuardrailsConfig()


@dataclass
class ModerationVerdict:
    """Outcome of one moderation call.

    Attributes:
        flagged: True when the content should be blocked.
        categories: Category → score for the categories at/over threshold.
        provider: Name of the provider that produced the verdict.
    """

    flagged: bool
    categories: dict[str, float] = field(default_factory=dict)
    provider: str = ""


def _resolve_scores(result: Any) -> dict[str, float]:
    """Normalize a provider result's category scores to a plain dict."""
    raw = getattr(result, "category_scores", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        items = raw.items()
    else:
        dump = getattr(raw, "model_dump", None)
        if not callable(dump):
            return {}
        items = dump().items()
    scores: dict[str, float] = {}
    for name, value in items:
        if isinstance(value, (int, float)):
            scores[str(name)] = float(value)
    return scores


class OpenAIModerator:
    """Moderator backed by the OpenAI moderation API."""

    provider_name = "openai"

    def __init__(
        self,
        client: Any | None = None,
        api_key: Any | None = None,
        model: str = _DEFAULT_OPENAI_MODEL,
        threshold: float | None = None,
    ) -> None:
        """
        Args:
            client: Pre-built AsyncOpenAI-compatible client (tests/DI).
            api_key: API key used to build the client lazily when none given.
            model: Moderation model name.
            threshold: Score at/over which a category flags the content;
                defaults to ``GuardrailsConfig.moderation_threshold``.
        """
        self._client = client
        self._api_key = api_key
        self._model = model
        self._threshold = (
            threshold
            if threshold is not None
            else get_guardrails_config().moderation_threshold
        )

    def _ensure_client(self) -> Any:
        if self._client is None:
            import openai  # optional at import time; required to moderate

            key = self._api_key
            secret = getattr(key, "get_secret_value", None)
            if callable(secret):
                key = secret()
            self._client = openai.AsyncOpenAI(api_key=key)
        return self._client

    async def moderate(self, text: str) -> ModerationVerdict:
        """Moderate ``text``; flagged when the API flags it or any category
        score reaches the threshold."""
        client = self._ensure_client()
        response = await client.moderations.create(
            model=self._model, input=text[:_MAX_MODERATION_CHARS]
        )
        result = response.results[0]
        scores = _resolve_scores(result)
        over = {
            name: score for name, score in scores.items() if score >= self._threshold
        }
        flagged = bool(getattr(result, "flagged", False)) or bool(over)
        return ModerationVerdict(
            flagged=flagged, categories=over, provider=self.provider_name
        )


@lru_cache(maxsize=1)
def get_moderator() -> Any | None:
    """Resolve the configured moderator, or ``None`` when moderation is off.

    ``BASELITH_MODERATION_PROVIDER=openai`` selects the OpenAI moderation API,
    keyed from the central LLM config (``OPENAI_API_KEY`` / ``LLM_API_KEY``
    when OpenAI is the default provider). Unset or unknown → ``None``.
    """
    provider = os.environ.get(_ENV_PROVIDER, "").strip().lower()
    if not provider:
        return None
    if provider != "openai":
        logger.warning("moderation_provider_unknown", extra={"provider": provider})
        return None
    try:
        from core.config.services import get_llm_config

        config = get_llm_config()
        key = config.openai_api_key or (
            config.api_key if config.provider == "openai" else None
        )
    except Exception:  # config unavailable in exotic contexts
        key = None
    if not key:
        logger.warning(
            "moderation_provider_openai_without_key: set OPENAI_API_KEY "
            "(or LLM_API_KEY with LLM_PROVIDER=openai)"
        )
        return None
    return OpenAIModerator(api_key=key)


__all__ = [
    "ModerationVerdict",
    "OpenAIModerator",
    "get_guardrails_config",
    "get_moderator",
]
