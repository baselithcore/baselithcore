"""Tests for plugin load-time admission gates (version compat + config schema)."""

from core.plugins.config_validation import (
    is_config_enforcement_enabled,
    validate_plugin_config,
)
from core.plugins.interface import Plugin, PluginMetadata
from core.plugins.load_gates import compat_gate, config_gate
from core.plugins.version import (
    check_plugin_compatibility,
    is_compat_enforcement_enabled,
)


class _FakePlugin(Plugin):
    """Plugin stub with injectable metadata and config schema."""

    def __init__(self, metadata: PluginMetadata, schema: dict | None = None):
        super().__init__()
        self._metadata = metadata
        self._schema = schema or {}

    @property
    def metadata(self) -> PluginMetadata:  # type: ignore[override]
        return self._metadata

    def get_config_schema(self) -> dict:
        return self._schema


def _md(**kwargs) -> PluginMetadata:
    base = {"name": "p", "version": "1.0.0"}
    base.update(kwargs)
    return PluginMetadata(**base)


class TestCheckPluginCompatibility:
    def test_no_constraints_is_compatible(self):
        assert check_plugin_compatibility(core_version="0.10.0") == []

    def test_min_core_too_high(self):
        problems = check_plugin_compatibility(
            core_version="0.10.0", min_core_version="0.11.0"
        )
        assert len(problems) == 1
        assert "requires core" in problems[0]

    def test_max_core_too_low(self):
        problems = check_plugin_compatibility(
            core_version="2.0.0", max_core_version="1.0.0"
        )
        assert len(problems) == 1

    def test_core_within_bounds(self):
        assert (
            check_plugin_compatibility(
                core_version="1.5.0",
                min_core_version="1.0.0",
                max_core_version="2.0.0",
            )
            == []
        )

    def test_missing_plugin_dependency(self):
        problems = check_plugin_compatibility(
            core_version="1.0.0",
            plugin_dependencies={"browser_agent": ">=0.1.0"},
            available_versions={},
        )
        assert "missing plugin dependency" in problems[0]

    def test_dependency_version_unsatisfied(self):
        problems = check_plugin_compatibility(
            core_version="1.0.0",
            plugin_dependencies={"browser_agent": ">=0.2.0"},
            available_versions={"browser_agent": "0.1.0"},
        )
        assert "does not satisfy" in problems[0]

    def test_dependency_satisfied(self):
        assert (
            check_plugin_compatibility(
                core_version="1.0.0",
                plugin_dependencies={"browser_agent": ">=0.1.0"},
                available_versions={"browser_agent": "0.1.0"},
            )
            == []
        )


class TestValidatePluginConfig:
    def test_empty_schema_is_noop(self):
        assert validate_plugin_config({}, {"anything": 1}) == []
        assert validate_plugin_config(None, None) == []

    def test_valid_config(self):
        schema = {
            "type": "object",
            "properties": {"port": {"type": "integer"}},
            "required": ["port"],
        }
        assert validate_plugin_config(schema, {"port": 8080}) == []

    def test_missing_required_field(self):
        schema = {
            "type": "object",
            "properties": {"port": {"type": "integer"}},
            "required": ["port"],
        }
        problems = validate_plugin_config(schema, {})
        assert len(problems) == 1
        assert "port" in problems[0]

    def test_wrong_type(self):
        schema = {"type": "object", "properties": {"port": {"type": "integer"}}}
        problems = validate_plugin_config(schema, {"port": "not-an-int"})
        assert len(problems) == 1

    def test_invalid_schema_reported(self):
        problems = validate_plugin_config({"type": "not-a-real-type"}, {})
        assert len(problems) == 1
        assert "invalid config schema" in problems[0]


class TestCompatGate:
    def test_compatible_passes(self):
        plugin = _FakePlugin(_md())
        assert compat_gate(plugin, {}) is True

    def test_incompatible_warns_only_by_default(self, monkeypatch):
        monkeypatch.delenv("BASELITH_ENFORCE_PLUGIN_COMPAT", raising=False)
        plugin = _FakePlugin(_md(min_core_version="999.0.0"))
        assert compat_gate(plugin, {}) is True

    def test_incompatible_skipped_when_enforced(self, monkeypatch):
        monkeypatch.setenv("BASELITH_ENFORCE_PLUGIN_COMPAT", "true")
        plugin = _FakePlugin(_md(min_core_version="999.0.0"))
        assert compat_gate(plugin, {}) is False
        assert is_compat_enforcement_enabled() is True


class TestConfigGate:
    def test_valid_config_passes(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        }
        plugin = _FakePlugin(_md(), schema=schema)
        assert config_gate(plugin, {"x": 1}) is True

    def test_invalid_warns_only_by_default(self, monkeypatch):
        monkeypatch.delenv("BASELITH_ENFORCE_PLUGIN_CONFIG", raising=False)
        schema = {"type": "object", "required": ["x"]}
        plugin = _FakePlugin(_md(), schema=schema)
        assert config_gate(plugin, {}) is True

    def test_invalid_skipped_when_enforced(self, monkeypatch):
        monkeypatch.setenv("BASELITH_ENFORCE_PLUGIN_CONFIG", "true")
        schema = {"type": "object", "required": ["x"]}
        plugin = _FakePlugin(_md(), schema=schema)
        assert config_gate(plugin, {}) is False
        assert is_config_enforcement_enabled() is True

    def test_broken_schema_hook_does_not_block(self):
        class _Broken(_FakePlugin):
            def get_config_schema(self):
                raise RuntimeError("boom")

        plugin = _Broken(_md())
        assert config_gate(plugin, {}) is True
