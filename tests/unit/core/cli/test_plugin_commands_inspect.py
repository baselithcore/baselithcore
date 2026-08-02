"""
Tests for the read-only CLI plugin inspection commands.

Covers: plugin logs, plugin tree, the enhanced validate command and the
enhanced status command.
"""

import json
from unittest.mock import MagicMock, patch

from ._plugin_commands_helpers import _make_config, _make_plugin

# ──────────────────────────────────────────
# TestPluginLogs
# ──────────────────────────────────────────


class TestPluginLogs:
    """Tests for plugin logs command."""

    def test_logs_no_log_dir(self, tmp_path, monkeypatch):
        """Test logs command when no logs/ directory exists."""
        monkeypatch.chdir(tmp_path)

        from core.cli.commands.plugin.logs import plugin_logs

        result = plugin_logs("test-plugin")
        assert result == 0

    def test_logs_matching_entries(self, tmp_path, monkeypatch):
        """Test logs command finds matching entries."""
        monkeypatch.chdir(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        log_content = "\n".join(
            [
                "2025-12-25 10:00:00 [INFO] plugins.test_plugin.agent: Agent starting",
                "2025-12-25 10:00:01 [ERROR] plugins.test_plugin.agent: Connection failed",
                "2025-12-25 10:00:02 [INFO] core.server: Request handled",
            ]
        )
        (logs_dir / "app.log").write_text(log_content)

        from core.cli.commands.plugin.logs import plugin_logs

        result = plugin_logs("test-plugin")
        assert result == 0

    def test_logs_level_filter(self, tmp_path, monkeypatch):
        """Test logs command with level filter."""
        monkeypatch.chdir(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        log_content = "\n".join(
            [
                "2025-12-25 10:00:00 [INFO] plugins.test_plugin: Info message",
                "2025-12-25 10:00:01 [ERROR] plugins.test_plugin: Error message",
            ]
        )
        (logs_dir / "app.log").write_text(log_content)

        from core.cli.commands.plugin.logs import plugin_logs

        result = plugin_logs("test-plugin", level="ERROR")
        assert result == 0

    def test_logs_json_output(self, tmp_path, monkeypatch, capsys):
        """Test logs command JSON output."""
        monkeypatch.chdir(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        log_line = json.dumps(
            {
                "timestamp": "2025-12-25T10:00:00",
                "level": "INFO",
                "module": "plugins.test_plugin",
                "message": "Agent ready",
            }
        )
        (logs_dir / "app.log").write_text(log_line + "\n")

        from core.cli.commands.plugin.logs import plugin_logs

        result = plugin_logs("test-plugin", json_output=True)
        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert len(data) >= 1

    def test_logs_invalid_level(self, tmp_path, monkeypatch):
        """Test logs command with invalid level."""
        monkeypatch.chdir(tmp_path)

        from core.cli.commands.plugin.logs import plugin_logs

        result = plugin_logs("test-plugin", level="INVALID")
        assert result == 1


# ──────────────────────────────────────────
# TestPluginTree
# ──────────────────────────────────────────


class TestPluginTree:
    """Tests for plugin tree command."""

    def test_tree_no_plugins(self, tmp_path, monkeypatch):
        """Test tree with no plugins directory."""
        monkeypatch.chdir(tmp_path)

        from core.cli.commands.plugin.tree import plugin_tree

        result = plugin_tree()
        assert result == 0

    def test_tree_all_plugins(self, tmp_path, monkeypatch):
        """Test tree showing all plugins."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "plugin-a",
            manifest={
                "name": "plugin-a",
                "version": "1.0.0",
                "description": "A",
                "plugin_dependencies": ["plugin-b"],
            },
        )
        _make_plugin(
            tmp_path,
            "plugin-b",
            manifest={
                "name": "plugin-b",
                "version": "0.5.0",
                "description": "B",
            },
        )

        from core.cli.commands.plugin.tree import plugin_tree

        result = plugin_tree()
        assert result == 0

    def test_tree_single_plugin(self, tmp_path, monkeypatch):
        """Test tree for a specific plugin."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "plugin-a",
            manifest={
                "name": "plugin-a",
                "version": "1.0.0",
                "description": "A",
            },
        )

        from core.cli.commands.plugin.tree import plugin_tree

        result = plugin_tree("plugin-a")
        assert result == 0

    def test_tree_not_found(self, tmp_path, monkeypatch):
        """Test tree for non-existent plugin."""
        monkeypatch.chdir(tmp_path)
        # Need at least one real plugin so manifests is non-empty
        _make_plugin(
            tmp_path,
            "existing-plugin",
            manifest={
                "name": "existing-plugin",
                "version": "1.0.0",
                "description": "A",
            },
        )

        from core.cli.commands.plugin.tree import plugin_tree

        result = plugin_tree("nonexistent")
        assert result == 1

    def test_tree_json_output(self, tmp_path, monkeypatch, capsys):
        """Test tree JSON output."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "plugin-a",
            manifest={
                "name": "plugin-a",
                "version": "1.0.0",
                "description": "A",
                "plugin_dependencies": ["plugin-b"],
            },
        )

        from core.cli.commands.plugin.tree import plugin_tree

        result = plugin_tree(json_output=True)
        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "plugin-a" in data


# ──────────────────────────────────────────
# TestPluginValidateEnhanced
# ──────────────────────────────────────────


class TestPluginValidateEnhanced:
    """Tests for the enhanced validate command."""

    def test_validate_full_pass(self, tmp_path, monkeypatch):
        """Test validate passes with correct plugin."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "good-plugin",
            manifest={
                "name": "good-plugin",
                "version": "0.1.0",
                "description": "A good plugin",
            },
        )

        from core.cli.commands.plugin.local import validate_local_plugin

        result = validate_local_plugin("good-plugin")
        assert result == 0

    def test_validate_missing_manifest_fields(self, tmp_path, monkeypatch):
        """Test validate fails with missing manifest fields."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "bad-plugin",
            manifest={
                "name": "bad-plugin",
                # missing version and description
            },
        )

        from core.cli.commands.plugin.local import validate_local_plugin

        result = validate_local_plugin("bad-plugin")
        assert result == 1

    def test_validate_no_manifest(self, tmp_path, monkeypatch):
        """Test validate fails without manifest."""
        monkeypatch.chdir(tmp_path)
        plugin_dir = tmp_path / "plugins" / "no-manifest"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.py").write_text(
            "from core.plugins.interface import Plugin\n"
            "class NoManifestPlugin(Plugin):\n"
            "    async def initialize(self, config): pass\n"
        )

        from core.cli.commands.plugin.local import validate_local_plugin

        result = validate_local_plugin("no-manifest")
        assert result == 1

    def test_validate_json_output(self, tmp_path, monkeypatch, capsys):
        """Test validate JSON output."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "test-plugin",
            manifest={
                "name": "test-plugin",
                "version": "0.1.0",
                "description": "Test",
            },
        )

        from core.cli.commands.plugin import local as local_mod
        from core.cli.commands.plugin import local_validate as validate_mod

        # Suppress Rich console output to avoid contaminating capsys
        with patch.object(validate_mod, "console", MagicMock()):
            local_mod.validate_local_plugin("test-plugin", json_output=True)
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "checks" in data
        assert data["valid"] in (True, False)


# ──────────────────────────────────────────
# TestPluginStatusEnhanced
# ──────────────────────────────────────────


class TestPluginStatusEnhanced:
    """Tests for the enhanced status command."""

    def test_status_with_config_alignment(self, tmp_path, monkeypatch):
        """Test status shows config alignment info."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "aligned-plugin",
            manifest={
                "name": "aligned-plugin",
                "version": "0.1.0",
                "description": "Test",
                "readiness": "stable",
            },
        )
        _make_config(tmp_path, {"aligned-plugin": {"enabled": True}})

        from core.cli.commands.plugin import local as local_mod
        from core.cli.commands.plugin import local_shared

        with patch.object(
            local_shared, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = local_mod.status_local_plugins()
            assert result == 0

    def test_status_json_enhanced(self, tmp_path, monkeypatch, capsys):
        """Test status JSON includes new fields."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "test-plugin",
            manifest={
                "name": "test-plugin",
                "version": "1.0.0",
                "description": "Test",
                "readiness": "beta",
            },
        )
        _make_config(tmp_path, {"test-plugin": {"enabled": True}})

        from core.cli.commands.plugin import local as local_mod
        from core.cli.commands.plugin import local_shared

        with patch.object(
            local_shared, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = local_mod.status_local_plugins(json_output=True)
            assert result == 0
            output = capsys.readouterr().out
            data = json.loads(output)
            plugin_data = data["plugins"][0]
            assert "readiness" in plugin_data
            assert "in_config" in plugin_data
            assert "config_enabled" in plugin_data
