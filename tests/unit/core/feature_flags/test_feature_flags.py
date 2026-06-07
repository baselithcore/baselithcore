"""Unit tests for runtime feature flags."""

import pytest

from core.feature_flags.manager import (
    EnvFeatureFlagProvider,
    FeatureFlag,
    FeatureFlagManager,
    get_feature_flags,
    is_enabled,
    register_flag_provider,
    reset_feature_flags,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_feature_flags()
    yield
    reset_feature_flags()


class TestFlagDefinition:
    def test_invalid_rollout_rejected(self):
        with pytest.raises(ValueError):
            FeatureFlag("x", rollout_percentage=101)
        with pytest.raises(ValueError):
            FeatureFlag("x", rollout_percentage=-1)


class TestEvaluation:
    def test_unregistered_uses_call_default(self):
        mgr = FeatureFlagManager()
        assert mgr.is_enabled("ghost") is False
        assert mgr.is_enabled("ghost", default=True) is True

    def test_registered_default(self):
        mgr = FeatureFlagManager()
        mgr.register(FeatureFlag("f", default=True))
        assert mgr.is_enabled("f") is True
        # Registered default wins over call-site default.
        assert mgr.is_enabled("f", default=False) is True

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("BASELITH_FLAG_F", "true")
        mgr = FeatureFlagManager()
        mgr.register(FeatureFlag("f", default=False))
        assert mgr.is_enabled("f") is True

    def test_env_override_false(self, monkeypatch):
        monkeypatch.setenv("BASELITH_FLAG_F", "off")
        mgr = FeatureFlagManager()
        mgr.register(FeatureFlag("f", default=True, rollout_percentage=100))
        assert mgr.is_enabled("f", subject="s1") is False


class TestRollout:
    def test_zero_percent_disabled(self):
        mgr = FeatureFlagManager()
        mgr.register(FeatureFlag("f", rollout_percentage=0))
        assert mgr.is_enabled("f", subject="anyone") is False

    def test_hundred_percent_enabled(self):
        mgr = FeatureFlagManager()
        mgr.register(FeatureFlag("f", rollout_percentage=100))
        assert mgr.is_enabled("f", subject="anyone") is True

    def test_rollout_is_deterministic(self):
        mgr = FeatureFlagManager()
        mgr.register(FeatureFlag("f", rollout_percentage=50))
        a = mgr.is_enabled("f", subject="tenant-42")
        b = mgr.is_enabled("f", subject="tenant-42")
        assert a == b  # same subject -> same outcome

    def test_rollout_distribution_approximate(self):
        mgr = FeatureFlagManager()
        mgr.register(FeatureFlag("f", rollout_percentage=30))
        enabled = sum(mgr.is_enabled("f", subject=f"s{i}") for i in range(2000))
        # Expect ~600; allow generous tolerance.
        assert 480 <= enabled <= 720

    def test_rollout_ignored_without_subject(self):
        mgr = FeatureFlagManager()
        mgr.register(FeatureFlag("f", default=False, rollout_percentage=100))
        # No subject -> falls back to default, not rollout.
        assert mgr.is_enabled("f") is False


class TestEnvProvider:
    def test_truthy_and_falsy(self, monkeypatch):
        p = EnvFeatureFlagProvider()
        monkeypatch.setenv("BASELITH_FLAG_A", "yes")
        monkeypatch.setenv("BASELITH_FLAG_B", "0")
        monkeypatch.setenv("BASELITH_FLAG_C", "garbage")
        assert p.get_override("a") is True
        assert p.get_override("b") is False
        assert p.get_override("c") is None  # unrecognized -> defer
        assert p.get_override("missing") is None


class TestGlobalAndBackends:
    def test_global_singleton_and_helper(self):
        get_feature_flags().register(FeatureFlag("g", default=True))
        assert is_enabled("g") is True

    def test_custom_backend_selected(self, monkeypatch):
        class AllOn:
            def get_override(self, name):
                return True

        register_flag_provider("allon", lambda: AllOn())
        monkeypatch.setenv("FEATURE_FLAGS_BACKEND", "allon")
        reset_feature_flags()
        assert is_enabled("whatever") is True

    def test_unknown_backend_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("FEATURE_FLAGS_BACKEND", "does-not-exist")
        reset_feature_flags()
        # Falls back to env provider; no override -> default False.
        assert is_enabled("x") is False
