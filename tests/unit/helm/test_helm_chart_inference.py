"""Chart wiring for the core inference services (TEI + Qdrant endpoints)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.unit.helm import CHART_DIR, containers, documents, render

TEI = ("--set", "inference.tei.enabled=true")


def _by_kind(rendered: str, kind: str) -> list[dict]:
    return [d for d in documents(rendered) if d["kind"] == kind]


def _config(rendered: str) -> dict:
    cm = next(
        d
        for d in _by_kind(rendered, "ConfigMap")
        if d["metadata"]["name"] == "release-baselithcore-config"
    )
    return cm["data"]


def test_default_render_deploys_no_model_server_and_sets_no_endpoint() -> None:
    out = render()
    assert not [
        d for d in _by_kind(out, "Deployment") if "tei" in d["metadata"]["name"]
    ]
    data = _config(out)
    assert not any(k.startswith("BASELITH_") for k in data)


def test_tei_enabled_deploys_two_servers_and_wires_both_urls() -> None:
    out = render(*TEI, "--set", "inference.qdrant.url=http://qdrant.data:6333")
    names = {d["metadata"]["name"] for d in _by_kind(out, "Deployment")}
    assert {
        "release-baselithcore-tei-embed",
        "release-baselithcore-tei-rerank",
    } <= names
    data = _config(out)
    assert data["BASELITH_EMBEDDING_URL"] == "http://release-baselithcore-tei-embed:80"
    assert data["BASELITH_RERANK_URL"] == "http://release-baselithcore-tei-rerank:80"
    assert data["BASELITH_QDRANT_URL"] == "http://qdrant.data:6333"


def test_operator_url_wins_over_the_chart_default() -> None:
    out = render(*TEI, "--set-string", "config.BASELITH_EMBEDDING_URL=http://mine:8081")
    data = _config(out)
    assert data["BASELITH_EMBEDDING_URL"] == "http://mine:8081"
    assert data["BASELITH_RERANK_URL"].startswith(
        "http://release-baselithcore-tei-rerank"
    )


def test_tei_pods_never_match_the_api_selector_or_service() -> None:
    out = render(*TEI)
    api_name = "release-baselithcore"
    for dep in [
        d for d in _by_kind(out, "Deployment") if "-tei-" in d["metadata"]["name"]
    ]:
        labels = dep["spec"]["template"]["metadata"]["labels"]
        assert labels["app.kubernetes.io/name"] != "baselithcore"
        assert labels["app.kubernetes.io/name"].startswith(api_name + "-tei-")
    api_service = next(
        d for d in _by_kind(out, "Service") if d["metadata"]["name"] == api_name
    )
    assert api_service["spec"]["selector"]["app.kubernetes.io/component"] == "api"


def test_tei_runs_restricted_on_an_unprivileged_port() -> None:
    out = render(*TEI)
    for dep in [
        d for d in _by_kind(out, "Deployment") if "-tei-" in d["metadata"]["name"]
    ]:
        (c,) = containers(dep)
        assert c["securityContext"]["allowPrivilegeEscalation"] is False
        assert c["securityContext"]["readOnlyRootFilesystem"] is True
        assert (
            dep["spec"]["template"]["spec"]["securityContext"]["runAsNonRoot"] is True
        )
        port = c["ports"][0]["containerPort"]
        assert port >= 1024 and str(port) in c["args"]
        assert {"name": "HF_HOME", "value": "/data"} in c["env"]


def test_model_cache_is_a_pvc_by_default_and_an_emptydir_when_off() -> None:
    on = render(*TEI)
    assert len(_by_kind(on, "PersistentVolumeClaim")) == 2
    off = render(*TEI, "--set", "inference.tei.persistence.enabled=false")
    assert not _by_kind(off, "PersistentVolumeClaim")
    dep = next(
        d for d in _by_kind(off, "Deployment") if "-tei-embed" in d["metadata"]["name"]
    )
    assert dep["spec"]["strategy"]["type"] == "RollingUpdate"


def test_network_policy_admits_only_this_releases_pods_to_tei() -> None:
    out = render(*TEI, "--set", "networkPolicy.enabled=true")
    pols = [
        p for p in _by_kind(out, "NetworkPolicy") if "-tei-" in p["metadata"]["name"]
    ]
    assert len(pols) == 2
    for pol in pols:
        (rule,) = pol["spec"]["ingress"]
        assert (
            rule["from"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
            == "baselithcore"
        )
        assert pol["spec"]["policyTypes"] == ["Ingress"]


def test_hf_token_comes_from_an_existing_secret() -> None:
    out = render(*TEI, "--set", "inference.tei.hfTokenSecret.name=hf")
    dep = next(
        d for d in _by_kind(out, "Deployment") if "-tei-embed" in d["metadata"]["name"]
    )
    (c,) = containers(dep)
    env = {e["name"]: e for e in c["env"]}
    assert env["HF_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "hf",
        "key": "HF_TOKEN",
    }


def test_schema_rejects_a_privileged_port_and_unknown_keys() -> None:
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm binary not available")
    for bad in ("inference.tei.port=80", "inference.tei.typo=1"):
        res = subprocess.run(
            [helm, "template", "r", str(CHART_DIR), "--set", bad],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert res.returncode != 0, bad
