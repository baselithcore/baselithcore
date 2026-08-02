"""
Tests for the CLI ``plugin deps`` commands.

Covers: deps check (satisfied / missing dep / missing env var / JSON /
plugin not found), deps install and requirement validation.
"""

import json
from unittest.mock import patch

from ._plugin_commands_helpers import _make_plugin

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

    def test_deps_install_rejects_pip_option_injection(self, tmp_path, monkeypatch):
        """A non-PEP-508 manifest entry (pip option) is refused, never installed."""
        monkeypatch.chdir(tmp_path)
        _make_plugin(
            tmp_path,
            "evil-plugin",
            manifest={
                "name": "evil-plugin",
                "version": "0.1.0",
                "description": "Test",
                "python_dependencies": ["--index-url=http://attacker.example/simple"],
            },
        )

        from core.cli.commands.plugin.deps import deps_install

        with (
            patch(
                "core.cli.commands.plugin.deps._check_python_dep", return_value=False
            ),
            patch("core.cli.commands.plugin.deps.subprocess.run") as mock_run,
        ):
            result = deps_install("evil-plugin", yes=True)
            assert result == 1
            mock_run.assert_not_called()  # pip is never invoked

    def test_is_valid_requirement(self):
        from core.cli.commands.plugin.deps import _is_valid_requirement

        assert _is_valid_requirement("requests>=2.0")
        assert _is_valid_requirement("mineru[pipeline]>=3.4.4,<4")
        assert not _is_valid_requirement("--index-url=http://x/simple")
        assert not _is_valid_requirement("-r requirements.txt")
        assert not _is_valid_requirement("")
        assert not _is_valid_requirement(None)
