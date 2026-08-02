"""
Tests for the CLI ``plugin config`` commands.

Covers: config show (all / specific / not found / JSON), config set
(including bool coercion), config get and config reset.
"""

import json
from unittest.mock import patch

import yaml

from ._plugin_commands_helpers import _make_config

# ──────────────────────────────────────────
# TestPluginConfig
# ──────────────────────────────────────────


class TestPluginConfig:
    """Tests for config show/set/get/reset commands."""

    def test_config_show_all(self, tmp_path, monkeypatch):
        """Test config show without specific plugin."""
        monkeypatch.chdir(tmp_path)
        _make_config(tmp_path, {"my-plugin": {"enabled": True, "max_retries": 3}})

        from core.cli.commands.plugin import config as config_mod

        with patch.object(
            config_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = config_mod.config_show()
            assert result == 0

    def test_config_show_specific(self, tmp_path, monkeypatch):
        """Test config show for a specific plugin."""
        monkeypatch.chdir(tmp_path)
        _make_config(tmp_path, {"my-plugin": {"enabled": True}})

        from core.cli.commands.plugin import config as config_mod

        with patch.object(
            config_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = config_mod.config_show("my-plugin")
            assert result == 0

    def test_config_show_not_found(self, tmp_path, monkeypatch):
        """Test config show for non-existent plugin."""
        monkeypatch.chdir(tmp_path)
        _make_config(tmp_path, {"my-plugin": {"enabled": True}})

        from core.cli.commands.plugin import config as config_mod

        with patch.object(
            config_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = config_mod.config_show("nonexistent")
            assert result == 1

    def test_config_set(self, tmp_path, monkeypatch):
        """Test setting a config value."""
        monkeypatch.chdir(tmp_path)
        _make_config(tmp_path, {"my-plugin": {"enabled": True}})

        from core.cli.commands.plugin import config as config_mod

        with patch.object(
            config_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = config_mod.config_set("my-plugin", "max_retries", "5")
            assert result == 0

            # Verify written
            with open(tmp_path / "configs" / "plugins.yaml") as f:
                data = yaml.safe_load(f)
            assert data["my-plugin"]["max_retries"] == 5

    def test_config_set_bool_coercion(self, tmp_path, monkeypatch):
        """Test config set coerces booleans correctly."""
        monkeypatch.chdir(tmp_path)
        _make_config(tmp_path, {})

        from core.cli.commands.plugin import config as config_mod

        with patch.object(
            config_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            config_mod.config_set("new-plugin", "enabled", "true")

            with open(tmp_path / "configs" / "plugins.yaml") as f:
                data = yaml.safe_load(f)
            assert data["new-plugin"]["enabled"] is True

    def test_config_get(self, tmp_path, monkeypatch, capsys):
        """Test getting a specific config value."""
        monkeypatch.chdir(tmp_path)
        _make_config(tmp_path, {"my-plugin": {"enabled": True, "retries": 3}})

        from core.cli.commands.plugin import config as config_mod

        with patch.object(
            config_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = config_mod.config_get("my-plugin", "retries")
            assert result == 0

    def test_config_get_missing_key(self, tmp_path, monkeypatch):
        """Test config get with non-existent key."""
        monkeypatch.chdir(tmp_path)
        _make_config(tmp_path, {"my-plugin": {"enabled": True}})

        from core.cli.commands.plugin import config as config_mod

        with patch.object(
            config_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = config_mod.config_get("my-plugin", "nonexistent_key")
            assert result == 1

    def test_config_reset(self, tmp_path, monkeypatch):
        """Test resetting a plugin config."""
        monkeypatch.chdir(tmp_path)
        _make_config(tmp_path, {"my-plugin": {"enabled": True, "extra": "val"}})

        from core.cli.commands.plugin import config as config_mod

        with patch.object(
            config_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = config_mod.config_reset("my-plugin")
            assert result == 0

            with open(tmp_path / "configs" / "plugins.yaml") as f:
                data = yaml.safe_load(f)
            assert data["my-plugin"] == {"enabled": False}

    def test_config_json_output(self, tmp_path, monkeypatch, capsys):
        """Test config show with JSON output."""
        monkeypatch.chdir(tmp_path)
        _make_config(tmp_path, {"my-plugin": {"enabled": True}})

        from core.cli.commands.plugin import config as config_mod

        with patch.object(
            config_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = config_mod.config_show(json_output=True)
            assert result == 0
            output = capsys.readouterr().out
            data = json.loads(output)
            assert "my-plugin" in data
