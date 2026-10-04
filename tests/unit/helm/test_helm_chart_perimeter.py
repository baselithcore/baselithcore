"""Chart perimeter: egress, TEI auth and egress, ingress edge guards, hooks.

Each test here stands in for a cluster-only failure: an API pod that could
reach the cloud metadata endpoint, a model server any pod in the cluster could
query, `/metrics` and `/admin` served to the internet with no edge throttle,
and two hook Jobs that hang a first install for their whole deadline.
"""

from __future__ import annotations

import pytest

from tests.unit.helm import PRODUCTION_VALUES, containers, documents, render

PROD = ("-f", str(PRODUCTION_VALUES))
TEI = ("--set", "inference.tei.enabled=true")


def _by_kind(rendered: str, kind: str) -> list[dict]:
    return [d for d in documents(rendered) if d["kind"] == kind]


def _named(rendered: str, kind: str, name: str) -> dict:
    return next(d for d in _by_kind(rendered, kind) if d["metadata"]["name"] == name)


def _env(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env") or []}


# --- egress -----------------------------------------------------------------


def test_production_overlay_restricts_egress() -> None:
    out = render(*PROD)
    policy = _named(out, "NetworkPolicy", "release-baselithcore")
    assert "Egress" in policy["spec"]["policyTypes"]
    blocks = [
        to["ipBlock"]
        for rule in policy["spec"]["egress"]
        for to in rule.get("to") or []
        if "ipBlock" in to
    ]
    assert blocks, "the internetHttps preset is not rendered"
    assert "169.254.0.0/16" in blocks[0]["except"]


# --- TEI --------------------------------------------------------------------


def test_tei_pods_carry_an_egress_policy_that_excludes_metadata() -> None:
    out = render(*TEI, "--set", "networkPolicy.enabled=true")
    for pol in [
        p for p in _by_kind(out, "NetworkPolicy") if "-tei-" in p["metadata"]["name"]
    ]:
        assert pol["spec"]["policyTypes"] == ["Ingress", "Egress"]
        dns = [
            r for r in pol["spec"]["egress"] if any(p["port"] == 53 for p in r["ports"])
        ]
        assert dns, "DNS must stay open or the Hub download never resolves"
        https = [
            to["ipBlock"]
            for r in pol["spec"]["egress"]
            for to in r.get("to") or []
            if "ipBlock" in to
        ]
        assert https and "169.254.0.0/16" in https[0]["except"]


def test_tei_egress_can_be_switched_off_for_an_air_gapped_mirror() -> None:
    out = render(
        *TEI,
        "--set",
        "networkPolicy.enabled=true",
        "--set",
        "inference.tei.networkPolicy.egress.enabled=false",
    )
    for pol in [
        p for p in _by_kind(out, "NetworkPolicy") if "-tei-" in p["metadata"]["name"]
    ]:
        assert pol["spec"]["policyTypes"] == ["Ingress"]


def test_tei_api_key_reaches_both_the_servers_and_the_app() -> None:
    out = render(
        *TEI,
        "--set",
        "inference.tei.apiKeySecret.name=tei-key",
        "--set",
        "worker.enabled=true",
    )
    for kind in ("embed", "rerank"):
        (tei,) = containers(
            _named(out, "Deployment", f"release-baselithcore-tei-{kind}")
        )
        assert _env(tei)["API_KEY"]["valueFrom"]["secretKeyRef"] == {
            "name": "tei-key",
            "key": "TEI_API_KEY",
        }
    for name in ("release-baselithcore", "release-baselithcore-worker"):
        app = [
            c
            for c in containers(_named(out, "Deployment", name))
            if c["name"] != "seed-from-image"
        ]
        (app,) = app
        env = _env(app)
        for var in ("BASELITH_EMBEDDING_API_KEY", "BASELITH_RERANK_API_KEY"):
            assert env[var]["valueFrom"]["secretKeyRef"] == {
                "name": "tei-key",
                "key": "TEI_API_KEY",
            }


def test_tei_without_a_key_sends_none() -> None:
    out = render(*TEI)
    (tei,) = containers(_named(out, "Deployment", "release-baselithcore-tei-embed"))
    assert "API_KEY" not in _env(tei)
    (api,) = containers(_named(out, "Deployment", "release-baselithcore"))
    assert "BASELITH_EMBEDDING_API_KEY" not in _env(api)


def test_tei_image_can_be_pinned_by_digest() -> None:
    digest = "sha256:" + "a" * 64
    out = render(*TEI, "--set", f"inference.tei.image.digest={digest}")
    (tei,) = containers(_named(out, "Deployment", "release-baselithcore-tei-embed"))
    assert tei["image"] == f"ghcr.io/huggingface/text-embeddings-inference@{digest}"


def test_tei_replicas_above_one_get_a_budget_and_a_spread() -> None:
    out = render(*TEI, "--set", "inference.tei.embedding.replicas=2")
    pdbs = {p["metadata"]["name"] for p in _by_kind(out, "PodDisruptionBudget")}
    assert "release-baselithcore-tei-embed" in pdbs
    assert "release-baselithcore-tei-rerank" not in pdbs, (
        "a budget over a single replica denies every eviction"
    )
    embed = _named(out, "Deployment", "release-baselithcore-tei-embed")
    spread = embed["spec"]["template"]["spec"]["topologySpreadConstraints"]
    assert spread[0]["labelSelector"]["matchLabels"]["app.kubernetes.io/name"] == (
        "release-baselithcore-tei-embed"
    )
    rerank = _named(out, "Deployment", "release-baselithcore-tei-rerank")
    assert "topologySpreadConstraints" not in rerank["spec"]["template"]["spec"]


# --- ingress edge guards ----------------------------------------------------


def test_production_overlay_keeps_metrics_off_the_internet() -> None:
    out = render(*PROD)
    guard = _named(out, "Ingress", "release-baselithcore-metrics-guard")
    annotations = guard["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/whitelist-source-range"] == (
        "127.0.0.1/32"
    )
    paths = {
        p["path"] for rule in guard["spec"]["rules"] for p in rule["http"]["paths"]
    }
    assert {"/metrics", "/v1/metrics"} <= paths
    assert guard["spec"].get("tls"), "the guard must terminate TLS like the main one"


def test_production_overlay_throttles_the_auth_paths_at_the_edge() -> None:
    out = render(*PROD)
    guard = _named(out, "Ingress", "release-baselithcore-auth-guard")
    annotations = guard["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/limit-rps"] == "1"
    assert annotations["nginx.ingress.kubernetes.io/limit-burst-multiplier"] == "20"
    # The streaming/body annotations of the main Ingress still apply here —
    # /admin serves the console SPA, which uploads nothing but must not 413.
    assert annotations["nginx.ingress.kubernetes.io/proxy-body-size"] == "10m"
    paths = {
        p["path"] for rule in guard["spec"]["rules"] for p in rule["http"]["paths"]
    }
    assert {"/admin", "/v1/admin", "/api/auth"} <= paths


def test_edge_guards_are_off_by_default_and_refuse_a_foreign_class() -> None:
    out = render("--set", "ingress.enabled=true")
    assert not [
        i for i in _by_kind(out, "Ingress") if i["metadata"]["name"].endswith("-guard")
    ]
    with pytest.raises(AssertionError, match="ingress-nginx"):
        render(
            "--set",
            "ingress.enabled=true",
            "--set",
            "ingress.className=traefik",
            "--set",
            "ingress.guards.enabled=true",
        )


# --- hook Jobs on a first install ------------------------------------------


def test_plugin_schema_job_gets_its_env_sources_without_migrations() -> None:
    out = render(
        "--set",
        "migrations.enabled=false",
        "--set",
        "database.pluginSchemaInit.enabled=true",
    )
    assert _named(out, "ConfigMap", "release-baselithcore-migrate-config")
    assert _named(out, "Secret", "release-baselithcore-migrate-secrets")
    assert not [
        j for j in _by_kind(out, "Job") if j["metadata"]["name"].endswith("-migrate")
    ]


def test_runtime_role_job_reads_the_hook_scoped_secret() -> None:
    out = render(
        "--set",
        "database.runtimeRole.enabled=true",
        "--set",
        "database.runtimeRole.name=app",
        "--set",
        "database.runtimeRole.adminSecret.name=pg-admin",
    )
    (job,) = containers(_named(out, "Job", "release-baselithcore-db-runtime-role"))
    ref = _env(job)["RUNTIME_PASSWORD"]["valueFrom"]["secretKeyRef"]
    # The release Secret is an ordinary resource Helm applies only after its
    # hooks — a first install referencing it hangs until the deadline.
    assert ref["name"] == "release-baselithcore-migrate-secrets"


# --- containers nothing else sizes -----------------------------------------


def test_seed_init_container_carries_resources() -> None:
    out = render(
        "--set",
        "seedFromImage[0].from=/app/configs/plugins.yaml",
        "--set",
        "seedFromImage[0].to=/tmp/plugins.yaml",
    )
    api = _named(out, "Deployment", "release-baselithcore")
    seed = next(c for c in containers(api) if c["name"] == "seed-from-image")
    assert seed["resources"]["requests"]["memory"]
    assert seed["resources"]["limits"]["memory"]
