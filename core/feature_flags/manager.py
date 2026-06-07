"""Runtime feature flags with percentage rollout and a pluggable backend.

Until now, behaviour could only be toggled via environment variables read at
startup. This module adds first-class feature flags that support:

- **Runtime toggles / kill-switches** — flip behaviour without a redeploy when
  backed by a dynamic provider.
- **Percentage rollout** — deterministically enable a flag for a stable subset
  of subjects (tenant/user/session) via hashing, for gradual rollout.
- **Pluggable backends** — the default reads from the environment; external
  systems (LaunchDarkly, Unleash, a DB) register a provider, keeping that code
  out of ``core`` per the Sacred Core rule.

Evaluation order for ``is_enabled(name, subject=...)``:

1. A provider override (e.g. env ``BASELITH_FLAG_<NAME>=true|false``) — wins.
2. Percentage rollout, if a ``rollout_percentage`` is configured and a
   ``subject`` is supplied.
3. The flag's registered ``default`` (or the call-site ``default``).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from core.observability.logging import get_logger

logger = get_logger(__name__)

_ENV_PREFIX = "BASELITH_FLAG_"


@dataclass
class FeatureFlag:
    """Definition of a feature flag.

    Attributes:
        name: Stable identifier (lower_snake_case by convention).
        default: Value when no override/rollout applies.
        rollout_percentage: 0–100; when >0 and a subject is provided, the flag is
            enabled for a deterministic ``rollout_percentage``% of subjects.
        description: Human-readable purpose (for dashboards/docs).
    """

    name: str
    default: bool = False
    rollout_percentage: int = 0
    description: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.rollout_percentage <= 100:
            raise ValueError("rollout_percentage must be between 0 and 100.")


@runtime_checkable
class FeatureFlagProvider(Protocol):
    """Resolves an explicit on/off override for a flag, or ``None``."""

    def get_override(self, name: str) -> Optional[bool]:
        """Return ``True``/``False`` to force the flag, or ``None`` to defer."""
        ...


class EnvFeatureFlagProvider:
    """Read overrides from ``BASELITH_FLAG_<NAME>`` environment variables.

    ``<NAME>`` is the upper-cased flag name. Truthy: ``1/true/yes/on``;
    falsy: ``0/false/no/off``. Anything else (or unset) defers (``None``).
    """

    _TRUE = frozenset({"1", "true", "yes", "on"})
    _FALSE = frozenset({"0", "false", "no", "off"})

    def get_override(self, name: str) -> Optional[bool]:
        raw = os.environ.get(f"{_ENV_PREFIX}{name.upper()}")
        if raw is None:
            return None
        value = raw.strip().lower()
        if value in self._TRUE:
            return True
        if value in self._FALSE:
            return False
        return None


def _rollout_bucket(name: str, subject: str) -> int:
    """Map (flag, subject) to a stable bucket in [0, 100).

    Uses a hash so the same subject always lands in the same bucket for a given
    flag — enabling/raising the percentage only ever adds subjects.
    """
    digest = hashlib.sha256(f"{name}:{subject}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 100


class FeatureFlagManager:
    """Registry + evaluator for feature flags."""

    def __init__(self, provider: Optional[FeatureFlagProvider] = None) -> None:
        self._provider: FeatureFlagProvider = provider or EnvFeatureFlagProvider()
        self._flags: dict[str, FeatureFlag] = {}

    def register(self, flag: FeatureFlag) -> None:
        """Register (or replace) a flag definition."""
        self._flags[flag.name] = flag
        logger.debug(
            "Registered feature flag %s (default=%s, rollout=%d%%)",
            flag.name,
            flag.default,
            flag.rollout_percentage,
        )

    def all(self) -> dict[str, FeatureFlag]:
        """Return a copy of the registered flag definitions."""
        return dict(self._flags)

    def is_enabled(
        self,
        name: str,
        *,
        subject: Optional[str] = None,
        default: Optional[bool] = None,
    ) -> bool:
        """Evaluate a flag.

        Args:
            name: Flag name.
            subject: Stable id (tenant/user/session) for percentage rollout.
            default: Fallback when the flag is unregistered and no override is
                set. Ignored if the flag is registered (its default is used).

        Returns:
            Whether the feature is enabled for this evaluation.
        """
        override = self._provider.get_override(name)
        if override is not None:
            return override

        flag = self._flags.get(name)
        rollout = flag.rollout_percentage if flag else 0
        if rollout > 0 and subject is not None:
            if rollout >= 100:
                return True
            return _rollout_bucket(name, subject) < rollout

        if flag is not None:
            return flag.default
        return default if default is not None else False


_manager: Optional[FeatureFlagManager] = None
_PROVIDER_FACTORIES: dict[str, Callable[[], FeatureFlagProvider]] = {}


def register_flag_provider(
    name: str, factory: Callable[[], FeatureFlagProvider]
) -> None:
    """Register an external flag-provider factory selectable via env.

    Set ``FEATURE_FLAGS_BACKEND=<name>`` to use it.
    """
    _PROVIDER_FACTORIES[name.lower()] = factory
    logger.debug("Registered feature-flag backend %r", name)


def _build_provider() -> FeatureFlagProvider:
    backend = os.environ.get("FEATURE_FLAGS_BACKEND", "env").lower()
    if backend == "env":
        return EnvFeatureFlagProvider()
    factory = _PROVIDER_FACTORIES.get(backend)
    if factory is not None:
        return factory()
    logger.warning(
        "Unknown FEATURE_FLAGS_BACKEND %r; falling back to env provider.", backend
    )
    return EnvFeatureFlagProvider()


# Built-in flags registered by default. Keep additive — new optional behaviour
# should ship behind a flag defaulting to its current (pre-flag) value.
_DEFAULT_FLAGS: tuple[FeatureFlag, ...] = ()


def get_feature_flags() -> FeatureFlagManager:
    """Return the process-wide feature-flag manager (built on first use)."""
    global _manager
    if _manager is None:
        _manager = FeatureFlagManager(provider=_build_provider())
        for flag in _DEFAULT_FLAGS:
            _manager.register(flag)
    return _manager


def reset_feature_flags() -> None:
    """Clear the cached manager. Intended for tests only."""
    global _manager
    _manager = None


def is_enabled(
    name: str, *, subject: Optional[str] = None, default: Optional[bool] = None
) -> bool:
    """Convenience accessor delegating to the global manager."""
    return get_feature_flags().is_enabled(name, subject=subject, default=default)


__all__ = [
    "FeatureFlag",
    "FeatureFlagProvider",
    "EnvFeatureFlagProvider",
    "FeatureFlagManager",
    "get_feature_flags",
    "reset_feature_flags",
    "register_flag_provider",
    "is_enabled",
]
