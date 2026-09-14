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


class TestPrometheusRule:
    """The alerts, and the one property that decides whether they are trusted.

    A suspended customer cell IS zero replicas with the data kept, and KEDA is
    allowed to scale the worker to zero on an empty queue. Both are desired
    states and both look exactly like an outage to kube-state-metrics, so every
    "nothing is running" alert has to be qualified by the deployment still
    *wanting* replicas. Get that wrong and every suspension pages somebody at
    3am for a cell that is off on purpose — which is how a team learns to close
    alerts without reading them.
    """

    @staticmethod
    def _rules(*args: str) -> list[dict]:
        rendered = TestChartRenders._render(
            "--set", "prometheusRule.enabled=true", *args
        )
        objects = [
            doc
            for doc in yaml.safe_load_all(rendered)
            if doc and doc["kind"] == "PrometheusRule"
        ]
        assert len(objects) == 1, [doc["metadata"]["name"] for doc in objects]
        groups = objects[0]["spec"]["groups"]
        assert len(groups) == 1, "one group keeps every expression on one instant"
        return groups[0]["rules"]

    def test_absent_by_default(self) -> None:
        """A rule object referencing a Prometheus that is not there is dead
        YAML, so the chart ships the alerts off."""
        kinds = {
            doc["kind"] for doc in yaml.safe_load_all(TestChartRenders._render()) if doc
        }
        assert "PrometheusRule" not in kinds

    def test_down_alerts_exempt_a_suspended_deployment(self) -> None:
        rules = self._rules("--set", "worker.enabled=true")
        down = [rule for rule in rules if rule["alert"].endswith("Down")]
        assert {rule["alert"] for rule in down} == {
            "BaselithcoreApiDown",
            "BaselithcoreWorkerDown",
        }
        for rule in down:
            expr = " ".join(rule["expr"].split())
            assert "kube_deployment_spec_replicas" in expr, rule["alert"]
            assert "> 0" in expr, rule["alert"]

    def test_ratio_alerts_survive_an_idle_deployment(self) -> None:
        """Two failure modes, one idiom, both silent.

        A ratio over a zero denominator is NaN, and NaN compares false — so a
        quiet deployment would switch the rule off exactly when a single failed
        request should trip it. And in PromQL an empty vector divided by
        anything is still empty, so with no errors at all the rule evaluates to
        nothing rather than to zero: indistinguishable from a rule that is not
        firing, which is how a broken rule hides. ``clamp_min`` answers the
        first, ``or vector(0)`` the second, and this is the idiom
        ``deploy/prometheus/slo-rules.yml`` already established for the same
        metric.
        """
        ratios = [rule for rule in self._rules() if rule["alert"].endswith("Ratio")]
        assert len(ratios) >= 2
        for rule in ratios:
            expr = " ".join(rule["expr"].split())
            assert "clamp_min(" in expr, rule["alert"]
            assert "or vector(0)" in expr, rule["alert"]

    def test_latency_excludes_the_routes_designed_to_be_slow(self) -> None:
        """The streaming routes stay open for minutes by design, so leaving
        them in the denominator turns the alert into a measure of how much
        streaming the deployment does."""
        rule = next(
            rule
            for rule in self._rules()
            if rule["alert"] == "BaselithcoreHttpLatencyHigh"
        )
        for route in ("/chat/stream", "/v1/chat/stream", "/runs/.*/events", "/mcp"):
            assert route in rule["expr"], route
        # `\{` is not a legal escape in a PromQL string literal, so the events
        # route cannot be matched by its `/runs/{run_id}/events` template.
        assert "run_id" not in rule["expr"]

    def test_every_alert_carries_a_severity_and_says_what_to_do(self) -> None:
        for rule in self._rules(
            "--set", "worker.enabled=true", "--set", "backup.enabled=true"
        ):
            assert rule["labels"]["severity"] in {"critical", "warning"}, rule["alert"]
            assert rule["annotations"]["summary"], rule["alert"]
            assert len(rule["annotations"]["description"]) > 40, rule["alert"]

    def test_optional_workloads_get_alerts_only_when_they_exist(self) -> None:
        default = {rule["alert"] for rule in self._rules()}
        assert "BaselithcoreWorkerDown" not in default
        assert "BaselithcoreBackupStale" not in default
        both = {
            rule["alert"]
            for rule in self._rules(
                "--set", "worker.enabled=true", "--set", "backup.enabled=true"
            )
        }
        assert {"BaselithcoreWorkerDown", "BaselithcoreBackupStale"} <= both

    def test_disabled_alerts_are_left_out(self) -> None:
        """Silencing in the values keeps the reason in git, where an
        Alertmanager silence does not."""
        rules = self._rules(
            "--set", "prometheusRule.disabledAlerts[0]=BaselithcoreHttpLatencyHigh"
        )
        assert "BaselithcoreHttpLatencyHigh" not in {rule["alert"] for rule in rules}
        assert "BaselithcoreApiDown" in {rule["alert"] for rule in rules}

    def test_a_stale_backup_fires_even_when_it_never_ran(self) -> None:
        """The absent() arm is the point: with only the age comparison the rule
        stays silent precisely when backups have never once succeeded, because
        the series it measures does not exist."""
        rule = next(
            rule
            for rule in self._rules("--set", "backup.enabled=true")
            if rule["alert"] == "BaselithcoreBackupStale"
        )
        assert "absent(" in rule["expr"]


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
