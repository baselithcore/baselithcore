"""The updater must never import the plugins package (it would load plugin code)."""

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[5]
_CHECK = (
    "bad = [m for m in sys.modules if m == 'plugins' or m.startswith('plugins.')]\n"
    "assert not bad, bad\n"
)


def test_executor_never_imports_plugins() -> None:
    code = (
        "import sys\n"
        "import core.plugin_updates.apply.executor, core.plugin_updates.apply._io\n"
        + _CHECK
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120, cwd=_ROOT)


def test_updater_never_imports_plugins() -> None:
    code = (
        "import sys\n"
        "import core.plugin_updates.apply.updater, core.plugin_updates.apply.executor\n"
        "import core.cli.commands.plugin_updater\n" + _CHECK
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120, cwd=_ROOT)


def test_cli_entry_skips_plugin_clis_and_never_starts_sentry(tmp_path: Path) -> None:
    """``baselith plugin-updater`` from the checkout (the unit's cwd) loads no plugin code.

    The CLI scans ``plugins/*/cli.py`` for every other command; the updater must
    skip that scan. It must not start Sentry either: schema-init's environment
    (owner DB credentials) is a local of the executor, and an error reporter
    capturing frame locals would ship it off-host.
    """
    code = (
        "import sys\n"
        "sys.argv = ['baselith', 'plugin-updater', 'status']\n"
        "from core.cli.__main__ import main\n"
        "assert main() == 0\n"
        "if 'sentry_sdk' in sys.modules:\n"
        "    import sentry_sdk\n"
        "    assert not sentry_sdk.get_client().is_active()\n" + _CHECK
    )
    env = {
        **os.environ,
        "UPDATE_APPLY_STATE_DIR": str(tmp_path),
        "SENTRY_DSN": "https://k@o0.ingest.sentry.io/0",
    }
    subprocess.run(
        [sys.executable, "-c", code], check=True, timeout=180, cwd=_ROOT, env=env
    )
