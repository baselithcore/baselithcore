"""The pod's termination grace must cover the whole shutdown sequence.

Kubernetes counts the preStop hook against ``terminationGracePeriodSeconds``,
then sends SIGTERM; uvicorn drains HTTP for ``GRACEFUL_SHUTDOWN_TIMEOUT`` and
only then runs the lifespan teardown, bounded by
``core.api._shutdown.TEARDOWN_BUDGET_S``. The chart used to grant 45s against
a 5s preStop, a 30s drain and a teardown whose per-step timeouts summed to
minutes, so SIGKILL landed before the usage ledger, the audit queue and the
pools were flushed. These tests read every number from where it is defined.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from core.api._shutdown import TEARDOWN_BUDGET_S

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "baselithcore"


def _grace() -> int:
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    return int(values["terminationGracePeriodSeconds"])


def _prestop_sleep() -> int:
    template = (CHART_DIR / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    match = re.search(r'preStop:.*?"sleep (\d+)"', template, re.DOTALL)
    assert match is not None, "deployment.yaml no longer has a preStop sleep"
    return int(match.group(1))


def _drain_default(path: Path, pattern: str) -> int:
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    assert match is not None, f"no GRACEFUL_SHUTDOWN_TIMEOUT default in {path}"
    return int(match.group(1))


def _drains() -> dict[str, int]:
    shell = r"GRACEFUL_SHUTDOWN_TIMEOUT:-(\d+)\}"
    return {
        "backend.py": _drain_default(
            REPO_ROOT / "backend.py",
            r'getenv\(\s*"GRACEFUL_SHUTDOWN_TIMEOUT"\s*,\s*"(\d+)"',
        ),
        "Dockerfile": _drain_default(REPO_ROOT / "Dockerfile", shell),
        "core-entrypoint.sh": _drain_default(
            REPO_ROOT / "deploy" / "docker" / "core-entrypoint.sh", shell
        ),
    }


def test_every_entry_point_drains_for_the_same_default() -> None:
    drains = _drains()
    assert len(set(drains.values())) == 1, drains


def test_grace_covers_prestop_drain_and_teardown() -> None:
    drain = max(_drains().values())
    needed = _prestop_sleep() + drain + TEARDOWN_BUDGET_S
    assert _grace() >= needed, (
        f"terminationGracePeriodSeconds={_grace()} < preStop {_prestop_sleep()}"
        f" + drain {drain} + teardown {TEARDOWN_BUDGET_S} = {needed}"
    )


def test_production_values_do_not_shrink_the_grace() -> None:
    prod = yaml.safe_load(
        (CHART_DIR / "values-production.yaml").read_text(encoding="utf-8")
    )
    grace = prod.get("terminationGracePeriodSeconds")
    if grace is not None:
        drain = max(_drains().values())
        assert int(grace) >= _prestop_sleep() + drain + TEARDOWN_BUDGET_S
