"""Runtime feature flags (toggles, percentage rollout, pluggable backends).

See :mod:`core.feature_flags.manager`. Flags are opt-in: with nothing registered
and no ``BASELITH_FLAG_*`` overrides, ``is_enabled`` returns the call-site
default, so framework behaviour is unchanged.
"""

from core.feature_flags.manager import (
    EnvFeatureFlagProvider,
    FeatureFlag,
    FeatureFlagManager,
    FeatureFlagProvider,
    get_feature_flags,
    is_enabled,
    register_flag_provider,
    reset_feature_flags,
)

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
