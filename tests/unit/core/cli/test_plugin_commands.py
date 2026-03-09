"""
Tests for CLI plugin management commands.

Covers: deps check/install, config show/set/get/reset, logs, tree,
enhanced validate, bulk enable/disable, and interactive create.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import yaml


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────


def _make_plugin(
    tmp_path: Path, name: str, manifest: dict | None = None, disabled: bool = False
):
    """Create a minimal plugin directory for testing."""
    plugin_dir = tmp_path / "plugins" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    plugin_py = "plugin.disabled" if disabled else "plugin.py"
    (plugin_dir / plugin_py).write_text(
        "from core.plugins.interface import Plugin\n"
        f"class {name.replace('-', '').title()}Plugin(Plugin):\n"
        "    async def initialize(self, config): pass\n"
    )

    if manifest:
        (plugin_dir / "manifest.yaml").write_text(
            yaml.dump(manifest, default_flow_style=False)
        )

    if not disabled:
        (plugin_dir / "__init__.py").write_text(f'"""{name} plugin."""\n')

    return plugin_dir


def _make_config(tmp_path: Path, config: dict):
    """Create a configs/plugins.yaml for testing."""
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "plugins.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    return config_path


# ──────────────────────────────────────────
# TestPluginDeps
# ──────────────────────────────────────────


class TestPluginDeps:
    """Tests for deps check and install commands."""

    def test_deps_check_all_satisfied(self, tmp_path, monkeypatch):
        """Test deps check when all dependencies are met."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "test-plugin",
            manifest={
                "name": "test-plugin",
                "version": "0.1.0",
                "description": "Test",
                "python_dependencies": ["yaml"],
                "environment_variables": [],
            },
        )

        from core.cli.commands.plugin.deps import deps_check

        with patch(
            "core.cli.commands.plugin.deps._check_python_dep", return_value=True
        ):
            result = deps_check("test-plugin")
            assert result == 0

    def test_deps_check_missing_python_dep(self, tmp_path, monkeypatch):
        """Test deps check with missing Python dependency."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "test-plugin",
            manifest={
                "name": "test-plugin",
                "version": "0.1.0",
                "description": "Test",
                "python_dependencies": ["nonexistent-package-xyz"],
            },
        )

        from core.cli.commands.plugin.deps import deps_check

        result = deps_check("test-plugin")
        assert result == 1

    def test_deps_check_missing_env_var(self, tmp_path, monkeypatch):
        """Test deps check with missing environment variable."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "test-plugin",
            manifest={
                "name": "test-plugin",
                "version": "0.1.0",
                "description": "Test",
                "environment_variables": ["NONEXISTENT_VAR_XYZ_12345"],
            },
        )

        from core.cli.commands.plugin.deps import deps_check

        result = deps_check("test-plugin")
        assert result == 1

    def test_deps_check_json_output(self, tmp_path, monkeypatch, capsys):
        """Test deps check with JSON output."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "test-plugin",
            manifest={
                "name": "test-plugin",
                "version": "0.1.0",
                "description": "Test",
                "python_dependencies": [],
            },
        )

        from core.cli.commands.plugin.deps import deps_check

        result = deps_check("test-plugin", json_output=True)
        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["all_satisfied"] is True

    def test_deps_check_plugin_not_found(self, tmp_path, monkeypatch):
        """Test deps check with non-existent plugin."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "plugins").mkdir()

        from core.cli.commands.plugin.deps import deps_check

        result = deps_check("nonexistent")
        assert result == 1

    def test_deps_install_no_missing(self, tmp_path, monkeypatch):
        """Test deps install when all deps are already installed."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "test-plugin",
            manifest={
                "name": "test-plugin",
                "version": "0.1.0",
                "description": "Test",
                "python_dependencies": ["yaml"],
            },
        )

        from core.cli.commands.plugin.deps import deps_install

        with patch(
            "core.cli.commands.plugin.deps._check_python_dep", return_value=True
        ):
            result = deps_install("test-plugin")
            assert result == 0


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
# TestPluginBulkOps
# ──────────────────────────────────────────


class TestPluginBulkOps:
    """Tests for bulk enable/disable operations."""

    def test_bulk_disable_all(self, tmp_path, monkeypatch):
        """Test disabling all plugins at once."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "plugin-a",
            manifest={"name": "a", "version": "0.1.0", "description": "A"},
        )
        _make_plugin(
            tmp_path,
            "plugin-b",
            manifest={"name": "b", "version": "0.1.0", "description": "B"},
        )
        _make_config(tmp_path, {})

        from core.cli.commands.plugin import local as local_mod
        from core.cli.commands.plugin import local_shared

        with patch.object(
            local_shared, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = local_mod.disable_local_plugin("", all_plugins=True)
            assert result == 0

            # Verify files renamed
            assert (tmp_path / "plugins" / "plugin-a" / "plugin.disabled").exists()
            assert (tmp_path / "plugins" / "plugin-b" / "plugin.disabled").exists()

    def test_bulk_enable_all(self, tmp_path, monkeypatch):
        """Test enabling all disabled plugins at once."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "plugin-a",
            disabled=True,
            manifest={"name": "a", "version": "0.1.0", "description": "A"},
        )
        _make_plugin(
            tmp_path,
            "plugin-b",
            disabled=True,
            manifest={"name": "b", "version": "0.1.0", "description": "B"},
        )
        _make_config(tmp_path, {})

        from core.cli.commands.plugin import local as local_mod
        from core.cli.commands.plugin import local_shared

        with patch.object(
            local_shared, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            result = local_mod.enable_local_plugin("", all_plugins=True)
            assert result == 0

            # Verify files renamed back
            assert (tmp_path / "plugins" / "plugin-a" / "plugin.py").exists()
            assert (tmp_path / "plugins" / "plugin-b" / "plugin.py").exists()


# ──────────────────────────────────────────
# TestPluginCreateInteractive
# ──────────────────────────────────────────


class TestPluginCreateInteractive:
    """Tests for interactive plugin creation wizard."""

    def test_create_non_interactive(self, tmp_path, monkeypatch):
        """Test standard non-interactive creation still works."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "plugins").mkdir()

        from core.cli.commands.plugin.create import create_plugin

        result = create_plugin("my-new-plugin", "agent")
        assert result == 0
        assert (tmp_path / "plugins" / "my-new-plugin" / "plugin.py").exists()
        assert (tmp_path / "plugins" / "my-new-plugin" / "manifest.json").exists()

    def test_create_duplicate(self, tmp_path, monkeypatch):
        """Test creating a plugin with an existing name fails."""
        monkeypatch.chdir(tmp_path)
        plugin_dir = tmp_path / "plugins" / "existing-plugin"
        plugin_dir.mkdir(parents=True)

        from core.cli.commands.plugin.create import create_plugin

        result = create_plugin("existing-plugin", "agent")
        assert result == 1

    def test_create_interactive_wizard(self, tmp_path, monkeypatch):
        """Test interactive wizard prompts and creates plugin."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "plugins").mkdir()
        _make_config(tmp_path, {})

        from core.cli.commands.plugin import create as create_mod

        # Mock user inputs for the wizard
        inputs = iter(
            [
                "wizard-plugin",
                "agent",
                "My wizard plugin",
                "Test Author",
                "agent,wizard",
                "",
                "y",
            ]
        )

        with patch.object(
            create_mod, "PLUGINS_CONFIG_PATH", tmp_path / "configs" / "plugins.yaml"
        ):
            with patch("builtins.input", side_effect=lambda: next(inputs)):
                result = create_mod.create_plugin("", interactive=True)
                assert result == 0

        assert (tmp_path / "plugins" / "wizard-plugin" / "plugin.py").exists()


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


# ──────────────────────────────────────────
# TestPluginDispatcher
# ──────────────────────────────────────────


class TestPluginDispatcher:
    """Tests for CLI dispatcher routing to new plugin subcommands."""

    def test_cli_plugin_deps_dispatch(self):
        """Test CLI dispatches to plugin deps command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["baselith", "plugin", "deps", "check", "my-plugin"]):
            with patch("core.cli.__main__.cmd_plugin", return_value=0) as mock:
                main()
                mock.assert_called_once()

    def test_cli_plugin_config_dispatch(self):
        """Test CLI dispatches to plugin config command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["baselith", "plugin", "config", "show"]):
            with patch("core.cli.__main__.cmd_plugin", return_value=0) as mock:
                main()
                mock.assert_called_once()

    def test_cli_plugin_logs_dispatch(self):
        """Test CLI dispatches to plugin logs command."""
        from core.cli.__main__ import main

        with patch(
            "sys.argv", ["baselith", "plugin", "logs", "my-plugin", "--lines", "20"]
        ):
            with patch("core.cli.__main__.cmd_plugin", return_value=0) as mock:
                main()
                mock.assert_called_once()

    def test_cli_plugin_tree_dispatch(self):
        """Test CLI dispatches to plugin tree command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["baselith", "plugin", "tree"]):
            with patch("core.cli.__main__.cmd_plugin", return_value=0) as mock:
                main()
                mock.assert_called_once()
