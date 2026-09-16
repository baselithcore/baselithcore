"""Shared harness for the Helm chart tests.

The chart suite outgrew one module, and everything it needs in common is here:
where the chart lives, how to render it, and how to walk the manifests that
come back. Extracted as a package rather than a sibling module so the chart
tests import one name and the helper stays testable on its own.

Rendering from pytest needs no new CI job and no pinned action: GitHub's ubuntu
runner image ships helm, so these run in the existing test job and skip on a
host without the binary.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

__all__ = [
    "CHART",
    "CHART_DIR",
    "DEFAULT_VALUES",
    "PRODUCTION_VALUES",
    "REPO_ROOT",
    "containers",
    "documents",
    "load_yaml",
    "render",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "baselithcore"
CHART = CHART_DIR / "Chart.yaml"
PRODUCTION_VALUES = CHART_DIR / "values-production.yaml"
DEFAULT_VALUES = CHART_DIR / "values.yaml"


def load_yaml(path: Path) -> dict:
    """Parse a single YAML document off disk."""
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def render(*args: str) -> str:
    """Render the chart, failing the test with helm's own stderr.

    Args:
        *args: Extra arguments appended to ``helm template``.

    Returns:
        The rendered manifest stream.
    """
    helm = shutil.which("helm")
    if helm is None:  # pragma: no cover — depends on the host toolchain
        pytest.skip("helm binary not available")
    result = subprocess.run(
        [helm, "template", "release", str(CHART_DIR), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


def documents(rendered: str) -> list[dict]:
    """The non-empty manifests in a rendered stream."""
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def containers(doc: dict) -> list[dict]:
    """Every container of a rendered manifest, whatever kind wraps the pod."""
    spec = doc.get("spec") or {}
    pod_specs = [
        spec,  # Pod
        (spec.get("template") or {}).get("spec"),  # Deployment / Job
        ((spec.get("jobTemplate") or {}).get("spec", {}).get("template") or {}).get(
            "spec"
        ),  # CronJob
    ]
    return [
        container
        for pod_spec in pod_specs
        if isinstance(pod_spec, dict)
        for key in ("initContainers", "containers")
        for container in pod_spec.get(key) or []
    ]
