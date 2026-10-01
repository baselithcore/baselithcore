"""Tests for the /metrics router: auth toggle and multiprocess registry."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.config.security as security_config_module
import core.middleware.security as security_manager_module
from core.middleware.rate_limiter import RateLimiter

SCRAPE = ("prometheus", "scrape-only-password-0123456789")
ADMIN = ("admin", "admin-password-0123456789abcdef")


def _build_app() -> FastAPI:
    from plugins.api_routers import metrics as metrics_module

    app = FastAPI()
    app.include_router(metrics_module.router)
    return app


@pytest.fixture
def fresh_security_config(monkeypatch):
    """Force SecurityConfig re-read from env for each test.

    Each manager also gets its own rate-limit key prefix: with a real Redis
    (CI) the admin lockout counter for ``testclient`` outlives the test, so
    failed logins in earlier tests locked this source out of later ones.
    """
    original_init = RateLimiter.__init__
    prefix = f"test-{uuid.uuid4().hex}:"

    def isolated_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._prefix = prefix + self._prefix

    monkeypatch.setattr(RateLimiter, "__init__", isolated_init)
    monkeypatch.setattr(security_config_module, "_security_config", None)
    monkeypatch.setattr(security_manager_module, "_security_manager", None)
    yield
    monkeypatch.setattr(security_config_module, "_security_config", None)
    monkeypatch.setattr(security_manager_module, "_security_manager", None)


@pytest.fixture
def two_credentials(monkeypatch, fresh_security_config):
    """A scrape-only credential and a distinct admin one, auth required."""
    monkeypatch.delenv("METRICS_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ADMIN_PASS_HASHED", raising=False)
    monkeypatch.setenv("METRICS_USERNAME", SCRAPE[0])
    monkeypatch.setenv("METRICS_PASSWORD", SCRAPE[1])
    monkeypatch.setenv("ADMIN_USER", ADMIN[0])
    monkeypatch.setenv("ADMIN_PASS", ADMIN[1])


def _app_with_admin() -> FastAPI:
    from plugins.api_routers import admin as admin_module

    app = _build_app()
    app.include_router(admin_module.router)
    return app


def test_metrics_requires_auth_by_default(monkeypatch, fresh_security_config):
    monkeypatch.delenv("METRICS_AUTH_REQUIRED", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/metrics")
    assert resp.status_code == 401


def test_metrics_public_when_auth_disabled(monkeypatch, fresh_security_config):
    monkeypatch.setenv("METRICS_AUTH_REQUIRED", "false")
    client = TestClient(_build_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"# HELP" in resp.content or b"# TYPE" in resp.content


def test_metrics_uses_multiprocess_registry(
    monkeypatch, tmp_path, fresh_security_config
):
    monkeypatch.setenv("METRICS_AUTH_REQUIRED", "false")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    calls: dict[str, object] = {}
    from prometheus_client import multiprocess

    original = multiprocess.MultiProcessCollector

    class SpyCollector(original):  # type: ignore[misc,valid-type]
        def __init__(self, registry, path=None):
            calls["registry"] = registry
            super().__init__(registry, path=path or str(tmp_path))

    monkeypatch.setattr(multiprocess, "MultiProcessCollector", SpyCollector)
    client = TestClient(_build_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "registry" in calls


# --- OpenMetrics negotiation -------------------------------------------------
#
# The HTTP and GenAI histograms attach a ``trace_id`` exemplar to every sampled
# observation (``core.observability.metric_context``). Exemplars only exist in
# the OpenMetrics exposition format; the Prometheus text format has no syntax
# for them, so serving text/plain unconditionally threw away the entire feature
# — the data was recorded and then dropped on the way out.

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
OPENMETRICS_ACCEPT = (
    "application/openmetrics-text;version=1.0.0;q=0.75,"
    "text/plain;version=0.0.4;q=0.5,*/*;q=0.1"
)


@pytest.fixture
def exemplar_registry(monkeypatch):
    """Point the router at a registry holding one exemplar-bearing bucket."""
    from prometheus_client import CollectorRegistry, Histogram

    from plugins.api_routers import metrics as metrics_module

    registry = CollectorRegistry()
    histogram = Histogram(
        "demo_request_duration_seconds",
        "Demo latency.",
        registry=registry,
        buckets=(0.1, float("inf")),
    )
    histogram.observe(0.05, exemplar={"trace_id": TRACE_ID})
    monkeypatch.setattr(metrics_module, "REGISTRY", registry)
    return registry


def test_openmetrics_requested_serves_exemplars(
    monkeypatch, fresh_security_config, exemplar_registry
):
    """The recorded trace_id must actually reach the scraper's body."""
    monkeypatch.setenv("METRICS_AUTH_REQUIRED", "false")
    client = TestClient(_build_app())

    resp = client.get("/metrics", headers={"Accept": OPENMETRICS_ACCEPT})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/openmetrics-text")
    body = resp.content.decode()
    assert f'trace_id="{TRACE_ID}"' in body
    # OpenMetrics is a terminated format; a body without the trailer is
    # rejected outright by Prometheus.
    assert body.endswith("# EOF\n")


def test_plain_text_scraper_is_unaffected(
    monkeypatch, fresh_security_config, exemplar_registry
):
    """A 0.0.4-only scraper keeps getting parseable Prometheus text."""
    monkeypatch.setenv("METRICS_AUTH_REQUIRED", "false")
    client = TestClient(_build_app())

    resp = client.get(
        "/metrics", headers={"Accept": "text/plain;version=0.0.4;q=0.5,*/*;q=0.1"}
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.content.decode()
    assert "demo_request_duration_seconds_bucket" in body
    # The text format has no exemplar syntax: the `# {trace_id=...}` suffix
    # must not leak into it, or every 0.0.4 parser errors on the line.
    assert "trace_id=" not in body
    assert "# EOF" not in body


def test_missing_accept_header_defaults_to_plain_text(
    monkeypatch, fresh_security_config, exemplar_registry
):
    """No Accept header (curl, probes, older scrapers) stays on text/plain."""
    monkeypatch.setenv("METRICS_AUTH_REQUIRED", "false")
    client = TestClient(_build_app())

    resp = client.get("/metrics", headers={"Accept": ""})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# HELP" in resp.content.decode()


def test_openmetrics_negotiation_survives_multiprocess(
    monkeypatch, tmp_path, fresh_security_config
):
    """Multiprocess aggregation must still honour the negotiated encoding.

    prometheus_client's multiprocess collector cannot carry exemplars across
    the mmap files, so the body legitimately has none — but the content type
    must still be the one the scraper asked for, or the scraper hard-fails on
    a format mismatch.
    """
    monkeypatch.setenv("METRICS_AUTH_REQUIRED", "false")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    client = TestClient(_build_app())

    resp = client.get("/metrics", headers={"Accept": OPENMETRICS_ACCEPT})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/openmetrics-text")
    assert resp.content.decode().endswith("# EOF\n")


def test_plain_fallback_declares_the_classic_text_version(
    monkeypatch, fresh_security_config, exemplar_registry
):
    """Pin the exact content type an un-negotiated scrape gets.

    ``choose_encoder`` labels the fallback ``version=0.0.4`` rather than the
    ``version=1.0.0`` this endpoint used to hard-code. The bytes are identical
    (names are escaped to legacy-safe form by default); 0.0.4 is simply the
    claim every Prometheus release can parse, so the move is toward
    compatibility, not away from it. Asserted so a future bump is a decision.
    """
    monkeypatch.setenv("METRICS_AUTH_REQUIRED", "false")
    client = TestClient(_build_app())

    resp = client.get("/metrics", headers={"Accept": "*/*"})

    assert resp.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"


def test_scrape_credential_reads_metrics(two_credentials):
    resp = TestClient(_build_app()).get("/metrics", auth=SCRAPE)
    assert resp.status_code == 200


def test_scrape_username_with_wrong_password_is_401(two_credentials):
    resp = TestClient(_build_app()).get("/metrics", auth=(SCRAPE[0], "wrong"))
    assert resp.status_code == 401


def test_admin_credential_still_reads_metrics(two_credentials):
    """Backwards compatible: scrapers configured with the admin pair keep
    working."""
    resp = TestClient(_build_app()).get("/metrics", auth=ADMIN)
    assert resp.status_code == 200


def test_scrape_credential_grants_nothing_else(two_credentials):
    """The whole point of a dedicated credential: a Prometheus that holds it
    (and whoever can read its Secret) cannot open the admin surface."""
    client = TestClient(_app_with_admin())
    assert client.get("/admin", auth=SCRAPE).status_code == 401
    assert client.get("/admin/data", auth=SCRAPE).status_code == 401


def test_unset_scrape_password_accepts_no_scrape_login(
    monkeypatch, fresh_security_config
):
    """An empty METRICS_PASSWORD must not turn into "any password works"."""
    monkeypatch.delenv("METRICS_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("METRICS_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    monkeypatch.delenv("ADMIN_PASS_HASHED", raising=False)
    resp = TestClient(_build_app()).get("/metrics", auth=("metrics", ""))
    assert resp.status_code == 401
