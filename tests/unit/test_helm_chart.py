"""The Helm chart must ship the version of the code it sits next to.

``image.tag`` defaults to empty in ``values.yaml``, so ``Chart.appVersion`` is
not documentation — it is the image tag a plain ``helm install`` pulls. It had
drifted a release behind the codebase (``0.31.0`` against ``0.32.0``) because
semantic-release rewrote only ``core/_version.py``: the chart silently
deployed the previous release's image, and nothing in CI compared the two.

``.releaserc`` now rewrites the chart in the same release commit. These tests
are what notices if that ever stops working — a chart pointing at a tag that
was never built fails at pull time, in the cluster, long after the release.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core._version import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "baselithcore"
CHART = CHART_DIR / "Chart.yaml"
PRODUCTION_VALUES = CHART_DIR / "values-production.yaml"
DEFAULT_VALUES = CHART_DIR / "values.yaml"


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_chart_appversion_tracks_code_version() -> None:
    """``appVersion`` is the default image tag; it must be this release."""
    assert _load(CHART)["appVersion"] == __version__, (
        f"Chart.yaml appVersion != core.__version__ ({__version__}). A plain "
        "`helm install` pulls appVersion as the image tag, so a stale value "
        "deploys the wrong release. The release commit should rewrite it "
        "(see .releaserc); bump it by hand if you are changing it out of band."
    )


def test_default_values_inherit_the_chart_tag() -> None:
    """An explicit default tag would be a second place to bump, i.e. to drift."""
    assert _load(DEFAULT_VALUES)["image"]["tag"] == ""


def test_production_overlay_does_not_pin_a_stale_tag() -> None:
    """The overlay may inherit the chart tag or pin a digest — never a tag
    that has to be remembered at release time."""
    image = _load(PRODUCTION_VALUES)["image"]
    tag = image.get("tag", "")
    assert tag in ("", __version__), (
        f"values-production.yaml pins image.tag={tag!r} while this release is "
        f"{__version__}. Leave it empty to inherit Chart.appVersion, or set "
        "image.digest for a real (immutable) pin."
    )


def test_production_overlay_keeps_streams_alive_through_the_ingress() -> None:
    """Buffering off is only half of it: ingress-nginx's proxy-read-timeout
    defaults to 60s, which cuts any stream idle for a minute (an agent waiting
    on a slow LLM call) even with buffering disabled."""
    annotations = _load(PRODUCTION_VALUES)["ingress"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/proxy-buffering"] == "off"
    read_timeout = annotations["nginx.ingress.kubernetes.io/proxy-read-timeout"]
    assert int(read_timeout) >= 300


class TestChartRenders:
    """No CI job ran `helm lint` or `helm template` (Trivy's misconfig scan is
    report-only and cannot expand Go templates), so a template that failed to
    render — a tripped ``fail`` guard, a values key the schema rejects, invalid
    YAML — was discovered at ``helm install`` time, in the cluster.

    Rendering from pytest needs no new CI job and no pinned action: GitHub's
    ubuntu runner image ships helm (3.21.4 on ubuntu-24.04), so these run in
    the existing test job. They skip on a host without the binary.
    """

    @staticmethod
    def _render(*args: str) -> str:
        import shutil
        import subprocess

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

    def test_default_values_render(self) -> None:
        manifests = list(yaml.safe_load_all(self._render()))
        kinds = {doc["kind"] for doc in manifests if doc}
        assert {"Deployment", "Service", "ConfigMap"} <= kinds

    def test_production_values_render(self) -> None:
        manifests = [
            doc
            for doc in yaml.safe_load_all(self._render("-f", str(PRODUCTION_VALUES)))
            if doc
        ]
        images = {
            container["image"] for doc in manifests for container in _containers(doc)
        }
        # Every container of every kind — API, worker, migrate Job, smoke-test
        # Pod — must carry this release's tag, not a mix. The repository is
        # read from the values rather than hardcoded, so this file is valid in
        # any checkout of the chart.
        repository = _load(PRODUCTION_VALUES)["image"]["repository"]
        assert images == {f"{repository}:{__version__}"}, images

    def test_every_optional_branch_renders(self) -> None:
        """The branches a default render never reaches: backup CronJob, KEDA
        ScaledObject, the created Secret, multi-worker metrics wiring."""
        manifests = list(
            yaml.safe_load_all(
                self._render(
                    "-f",
                    str(PRODUCTION_VALUES),
                    "--set",
                    "backup.enabled=true",
                    "--set",
                    "worker.keda.enabled=true",
                    "--set",
                    "worker.keda.redis.address=falkordb.svc.cluster.local:6379",
                    "--set",
                    "secrets.create=true",
                    "--set",
                    "secrets.existingSecret=null",
                    "--set",
                    "tmpVolumes.enabled=true",
                    "--set",
                    "webConcurrency=4",
                )
            )
        )
        kinds = {doc["kind"] for doc in manifests if doc}
        assert {"CronJob", "ScaledObject", "Secret", "NetworkPolicy"} <= kinds
        # >1 uvicorn worker means /metrics must aggregate across processes.
        api = next(
            doc
            for doc in manifests
            if doc
            and doc["kind"] == "Deployment"
            and doc["metadata"]["labels"].get("app.kubernetes.io/component") == "api"
        )
        env = {
            item["name"]: item.get("value")
            for item in api["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        assert env["PROMETHEUS_MULTIPROC_DIR"] == "/tmp/prometheus"
        # Behind an ingress controller, an untrusted proxy header collapses
        # every per-IP control into one shared bucket.
        assert env["FORWARDED_ALLOW_IPS"]

    def test_keda_refuses_to_render_without_a_redis_address(self) -> None:
        """KEDA connects from the operator's own pod, so it cannot reuse the
        worker's QUEUE_REDIS_URL; the chart must fail rather than ship a
        ScaledObject that silently never scales."""
        import shutil
        import subprocess

        helm = shutil.which("helm")
        if helm is None:  # pragma: no cover — depends on the host toolchain
            pytest.skip("helm binary not available")
        result = subprocess.run(
            [
                helm,
                "template",
                "release",
                str(CHART_DIR),
                "--set",
                "worker.enabled=true",
                "--set",
                "worker.keda.enabled=true",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode != 0
        assert "worker.keda.redis.address" in result.stderr


def _containers(doc: dict) -> list[dict]:
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
