"""
Tests for the CLI plugin lifecycle commands and dispatcher routing.

Covers: bulk enable/disable, interactive and non-interactive create, and
the ``baselith plugin ...`` argv dispatch.
"""

from unittest.mock import patch

from ._plugin_commands_helpers import _make_config, _make_plugin

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

    def test_create_class_name_splits_on_dash_and_underscore(
        self, tmp_path, monkeypatch
    ):
        """Underscore directory names get the same CamelCase class as dashed ones."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "plugins").mkdir()

        from core.cli.commands.plugin.create import create_plugin

        assert create_plugin("weather_agent", "agent") == 0
        plugin_py = (tmp_path / "plugins" / "weather_agent" / "plugin.py").read_text()
        assert "class WeatherAgentPlugin" in plugin_py
        assert "Weather_agent" not in plugin_py

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
