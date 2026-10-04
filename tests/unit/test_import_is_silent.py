"""Importing the framework writes nothing to the host program's output.

Unconfigured structlog prints every event, debug included, to stdout, so
``import baselith`` followed by touching the facade used to print lines such
as ``[debug] CacheMetricsCollector initialized``. A library leaves output to
the application; these tests run in a fresh interpreter from a directory
outside the checkout, so no ``.env`` or logging setup leaks in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = """
import baselith
for name in baselith.__all__:
    getattr(baselith, name)
from core.config.plugins import get_plugin_config
get_plugin_config()
"""


def _run(probe: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    env.pop("LOG_LEVEL", None)
    return subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=120,
        check=True,
    )


def test_importing_the_facade_prints_nothing(tmp_path: Path) -> None:
    result = _run(_PROBE, tmp_path)
    assert result.stdout == ""
    assert result.stderr == ""


def test_app_logging_setup_still_emits(tmp_path: Path) -> None:
    """Configuring logging after import still routes framework loggers."""
    probe = """
import io
from core.observability.logging import configure_logging, get_logger, _stop_log_listener
log = get_logger("probe")  # created before the app configures logging
buf = io.StringIO()
configure_logging(level="INFO", json_output=True, stream=buf)
log.info("probe_event", answer=42)
_stop_log_listener()
print(buf.getvalue())
"""
    result = _run(probe, tmp_path)
    assert '"event": "probe_event"' in result.stdout
    assert '"answer": 42' in result.stdout
