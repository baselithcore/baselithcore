"""``--format json`` reaches every handler that can emit JSON.

Regression: ``verify``, ``doctor`` and ``info`` read only their local
``--json`` flag, so the global ``--format json`` every subcommand accepts was
silently ignored and text was printed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.cli.__main__ import main


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("info", "core.cli.commands.info.run_info"),
        ("verify", "core.cli.commands.verify.run_verify"),
        ("doctor", "core.cli.commands.doctor.run_doctor"),
    ],
)
@pytest.mark.parametrize("flag", [["--format", "json"], ["--json"]])
def test_json_requested_either_way(command: str, target: str, flag: list[str]):
    if flag == ["--json"] and command == "verify":
        pytest.skip("verify declares no local --json flag")
    with (
        patch("sys.argv", ["baselith", command, *flag]),
        patch(target, return_value=0) as run,
    ):
        main()
    assert run.call_args.kwargs["json_output"] is True


class TestSyncEnableRuleMatchesRuntime:
    """``plugin sync`` decides "enabled" exactly as the runtime loader does."""

    def _write(self, tmp_path, monkeypatch, text: str | None) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PLUGIN_CONFIG_PATH", raising=False)
        if text is not None:
            (tmp_path / "configs").mkdir()
            (tmp_path / "configs" / "plugins.yaml").write_text(text)

    def test_block_without_enabled_key_is_enabled(self, tmp_path, monkeypatch):
        from core.cli.commands.plugin.add_docker import _plugin_enabled

        self._write(tmp_path, monkeypatch, "api_routers: {}\noff:\n  enabled: false\n")
        assert _plugin_enabled("api_routers")
        assert _plugin_enabled("api-routers")  # dash/underscore variant
        assert not _plugin_enabled("off")
        assert not _plugin_enabled("absent")

    def test_no_config_file_enables_everything(self, tmp_path, monkeypatch):
        from core.cli.commands.plugin.add_docker import _plugin_enabled

        self._write(tmp_path, monkeypatch, None)
        assert _plugin_enabled("anything")
