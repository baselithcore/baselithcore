"""Helm chart: the network perimeter the production overlay ships with.

`networkPolicy.enabled` with the empty `ingressFrom` default admits only pods
in the release's namespace, while the ingress controller and Prometheus
usually live in their own — so the overlay went dark at the ingress and every
ServiceMonitor target went red the moment it was applied. The chart now
refuses that combination, and the overlay names both callers.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "baselithcore"
PRODUCTION_VALUES = CHART_DIR / "values-production.yaml"


def _helm_template(*args: str) -> subprocess.CompletedProcess[str]:
    helm = shutil.which("helm")
    if helm is None:  # pragma: no cover — depends on the host toolchain
        pytest.skip("helm binary not available")
    return subprocess.run(
        [helm, "template", "release", str(CHART_DIR), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_production_overlay_names_the_ingress_controller_and_prometheus() -> None:
    values = yaml.safe_load(PRODUCTION_VALUES.read_text(encoding="utf-8"))
    peers = values["networkPolicy"]["ingressFrom"]
    namespaces = {
        peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
        for peer in peers
    }
    assert {"ingress-nginx", "monitoring"} <= namespaces


def test_production_overlay_body_cap_matches_the_app() -> None:
    annotations = yaml.safe_load(PRODUCTION_VALUES.read_text(encoding="utf-8"))[
        "ingress"
    ]["annotations"]
    # = MAX_REQUEST_SIZE_BYTES default (10 MiB); ingress-nginx defaults to 1m.
    assert annotations["nginx.ingress.kubernetes.io/proxy-body-size"] == "10m"


@pytest.mark.parametrize(
    "switch", ["ingress.enabled=true", "serviceMonitor.enabled=true"]
)
def test_policy_without_peers_refuses_to_render(switch: str) -> None:
    result = _helm_template("--set", "networkPolicy.enabled=true", "--set", switch)
    assert result.returncode != 0
    assert "networkPolicy.ingressFrom" in result.stderr


def test_policy_without_external_callers_still_renders() -> None:
    result = _helm_template("--set", "networkPolicy.enabled=true")
    assert result.returncode == 0, result.stderr[-2000:]


def test_backup_containers_carry_resources() -> None:
    result = _helm_template(
        "--set",
        "backup.enabled=true",
        "--set",
        "backup.offsite.enabled=true",
        "--set",
        "backup.offsite.path=bucket/prefix",
        "--set",
        "backup.offsite.remote.type=s3",
        "--show-only",
        "templates/backup-cronjob.yaml",
    )
    assert result.returncode == 0, result.stderr[-2000:]
    pod = yaml.safe_load(result.stdout)["spec"]["jobTemplate"]["spec"]["template"][
        "spec"
    ]
    containers = pod["initContainers"] + pod["containers"]
    assert {c["name"] for c in containers} == {
        "wait-for-db",
        "pg-backup",
        "offsite-upload",
    }
    for container in containers:
        assert container["resources"]["requests"], container["name"]
        assert container["resources"]["limits"]["memory"], container["name"]
