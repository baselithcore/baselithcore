"""The shared plugin-config reader and the enable-list rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.plugins.config_file import (
    PLUGIN_CONFIG_PATH_ENV,
    plugin_enabled,
    read_plugin_configs,
    resolve_plugin_config_path,
)


class TestResolvePath:
    def test_default_is_configs_plugins_yaml_under_cwd(self, tmp_path, monkeypatch):
        monkeypatch.delenv(PLUGIN_CONFIG_PATH_ENV, raising=False)
        assert (
            resolve_plugin_config_path(tmp_path)
            == (tmp_path / "configs" / "plugins.yaml").resolve()
        )

    def test_relative_override_stays_under_cwd(self, tmp_path, monkeypatch):
        monkeypatch.setenv(PLUGIN_CONFIG_PATH_ENV, "data/configs/plugins.yaml")
        assert (
            resolve_plugin_config_path(tmp_path)
            == (tmp_path / "data" / "configs" / "plugins.yaml").resolve()
        )

    def test_escaping_path_is_refused(self, tmp_path, monkeypatch):
        outside = tmp_path.parent / "elsewhere.yaml"
        monkeypatch.setenv(PLUGIN_CONFIG_PATH_ENV, str(outside))
        with pytest.raises(ValueError, match="must resolve inside"):
            resolve_plugin_config_path(tmp_path)


class TestReadConfigs:
    def test_missing_file_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv(PLUGIN_CONFIG_PATH_ENV, raising=False)
        assert read_plugin_configs(tmp_path) == {}

    def test_reads_mapping(self, tmp_path, monkeypatch):
        monkeypatch.delenv(PLUGIN_CONFIG_PATH_ENV, raising=False)
        cfg = tmp_path / "configs" / "plugins.yaml"
        cfg.parent.mkdir()
        cfg.write_text("auth:\n  enabled: true\nwikigen:\n  enabled: false\n")
        assert read_plugin_configs(tmp_path) == {
            "auth": {"enabled": True},
            "wikigen": {"enabled": False},
        }

    def test_escaping_or_malformed_never_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv(PLUGIN_CONFIG_PATH_ENV, str(Path("/") / "nope.yaml"))
        assert read_plugin_configs(tmp_path) == {}
        monkeypatch.delenv(PLUGIN_CONFIG_PATH_ENV)
        cfg = tmp_path / "configs" / "plugins.yaml"
        cfg.parent.mkdir()
        cfg.write_text("- just\n- a list\n")
        assert read_plugin_configs(tmp_path) == {}


class TestEnableRule:
    def test_empty_config_enables_everything(self):
        assert plugin_enabled({}, "wikigen", "wikigen") is True

    def test_named_plugins_only_when_config_is_non_empty(self):
        configs = {"auth": {"enabled": True}}
        assert plugin_enabled(configs, "auth", "auth") is True
        assert plugin_enabled(configs, "wikigen", "wikigen") is False

    def test_enabled_false_wins(self):
        assert plugin_enabled({"auth": {"enabled": False}}, "auth", "auth") is False

    def test_directory_and_manifest_name_variants_match(self):
        configs = {"blog-forge": {"enabled": True}}
        assert plugin_enabled(configs, "blog_forge", "blog-forge") is True
        configs = {"document_sources": {}}
        assert plugin_enabled(configs, "document_sources", "document-sources") is True


def test_shipped_config_enables_api_routers(monkeypatch):
    # The shipped file is non-empty, so a plugin it omits is never loaded.
    # ``api_routers`` carries prompts, WebSocket chat, async runs, webhooks,
    # privacy, compliance, approvals and run controls: dropping it from the
    # file silently 404s all of them.
    monkeypatch.delenv(PLUGIN_CONFIG_PATH_ENV, raising=False)
    repo_root = Path(__file__).resolve().parents[4]
    configs = read_plugin_configs(repo_root)
    assert plugin_enabled(configs, "api_routers", "api-routers")
