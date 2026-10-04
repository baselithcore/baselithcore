"""The nginx edge and the production compose stack, read from the files.

Every assertion stands in for something only a running gateway shows: an
error page served without the security headers (one `add_header` inside a
location cancels every inherited one), a CSP whose script hash no longer
matches the page it protects, a 429 that comes back as stock HTML to an SDK
expecting RFC 9457, a worker flagged unhealthy by a probe it can never pass.

`nginx -t` and the live checks run only with a Docker daemon (see the sweep
notes); what can be read off the files is pinned here so it cannot drift
silently between those runs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_CONF = REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
ERRORS_DIR = REPO_ROOT / "deploy" / "nginx" / "errors"
COMPOSE_PROD = REPO_ROOT / "compose.prod.yaml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-image.yml"


def _conf() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """The directives, with the comment lines removed."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _block(text: str, header: str) -> str:
    """Body of the first `{header} {` block, at brace depth one."""
    start = text.index(header)
    start = text.index("{", start)
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    raise AssertionError(f"unterminated block {header!r}")


def _add_headers(block: str) -> dict[str, str]:
    return dict(re.findall(r"add_header\s+(\S+)\s+(.+?)\s+always;", block))


# --- security headers on the edge's own responses ---------------------------


#: Headers only the edge's own error documents carry. Each is set at server
#: level from a map on $uri that is empty everywhere but /__errors/, so a
#: proxied response never receives a second CSP and no location needs an
#: add_header of its own.
EDGE_PAGE_HEADERS = (
    "Content-Security-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
    "Cache-Control",
)


def _map_entries(code: str, variable: str) -> dict[str, str]:
    block = _block(code, f"map $uri ${variable}")
    return dict(re.findall(r"^\s*(\S+)\s+(.+?);\s*$", block, flags=re.M))


def test_no_location_declares_its_own_add_header() -> None:
    """One add_header inside a location cancels every header inherited from
    the server block, so the edge sets them all at server level and keeps the
    locations bare (also what Semgrep's header-redefinition rule asks)."""
    server = _block(_code(_conf()), "\n    server {")
    blocks = re.findall(
        r"^ +location\b[^{]*\{.*?\n        \}", server, flags=re.S | re.M
    )
    assert blocks, "no location block found"
    for location in blocks:
        assert "add_header" not in location, location.splitlines()[0]


def test_error_documents_get_their_own_headers_and_nothing_else_does() -> None:
    code = _code(_conf())
    server_headers = _add_headers(_block(code, "\n    server {"))
    for name in EDGE_PAGE_HEADERS:
        value = server_headers.get(name, "")
        assert value.startswith("$"), f"{name} must come from a $uri map"
        entries = _map_entries(code, value[1:])
        assert entries.get("default") == '""', (
            f"{name}: a non-empty default would stamp every proxied response"
        )
        assert any(key.startswith("~^/__errors/") for key in entries), name


class _ScriptCollector(HTMLParser):
    """Every <script> element, parsed rather than matched.

    The browser hashes the exact text between the tags, so the test reads it
    the way a parser does: tag names in any case, attributes allowed, nothing
    a regular expression would miss.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags = 0
        self.bodies: list[str] = []
        self._current: list[str] | None = None

    @classmethod
    def of(cls, html: str) -> _ScriptCollector:
        collector = cls()
        collector.feed(html)
        collector.close()
        return collector

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.tags += 1
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.bodies.append("".join(self._current))
            self._current = None


def _inline_scripts(html: str) -> list[str]:
    return _ScriptCollector.of(html).bodies


def test_csp_hash_matches_the_inline_script_of_the_outage_page() -> None:
    html = (ERRORS_DIR / "50x.html").read_text(encoding="utf-8")
    scripts = _inline_scripts(html)
    assert len(scripts) == 1
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
    code = _code(_conf())
    variable = _add_headers(_block(code, "\n    server {"))["Content-Security-Policy"]
    entries = _map_entries(code, variable[1:])
    csp = next(v for k, v in entries.items() if k.startswith("~^/__errors/"))
    assert f"'sha256-{digest}'" in csp, (
        "50x.html's inline script changed; the CSP hash in nginx.conf must "
        "be recomputed or the retry logic stops running."
    )
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert not _ScriptCollector.of((ERRORS_DIR / "4xx.html").read_text()).tags, (
        "4xx.html must stay script-free: it carries no hash in the CSP"
    )


def test_edge_rejections_get_a_problem_body_and_the_outage_pages_stay() -> None:
    code = _code(_conf())
    assert re.search(r"error_page 502 503 504 \$edge_error_page;", code)
    assert re.search(r"error_page 413 429 \$edge_reject_page;", code)
    assert "proxy_intercept_errors" not in code, (
        "the app's own problem+json 4xx/5xx bodies must pass through untouched"
    )
    for name in ("4xx", "50x"):
        assert (ERRORS_DIR / f"{name}.html").exists()
        problem = (ERRORS_DIR / f"{name}.problem").read_text(encoding="utf-8")
        # Valid RFC 9457 once nginx's SSI has run: resolve the directives the
        # way `ssi on` does for a 429 / 502 and parse what is left.
        rendered = re.sub(r"<!--# echo var=\"status\"[^>]*-->", "429", problem)
        rendered = re.sub(
            r"<!--# if expr=\"\$status = 413\" -->.*?<!--# else -->(.*?)<!--# endif -->",
            r"\1",
            rendered,
            flags=re.S,
        )
        body = json.loads(rendered)
        assert {"type", "title", "status", "detail"} <= body.keys()
        assert body["status"] == 429


def test_metrics_never_leave_through_the_edge() -> None:
    location = _block(_code(_conf()), "location ~ ^(/v1)?/metrics/?$")
    assert "deny all;" in location
    assert "allow " not in location, (
        "a private-range allow-list admits the Docker bridge gateway, i.e. "
        "every client arriving through docker-proxy"
    )
    assert "proxy_pass" not in location


def test_gateway_serves_its_own_liveness() -> None:
    assert "return 204;" in _block(_code(_conf()), "location = /__gateway/healthz")


# --- compose.prod.yaml ------------------------------------------------------


def _services() -> dict[str, dict]:
    return yaml.safe_load(COMPOSE_PROD.read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize("service", ["api", "worker"])
def test_app_containers_run_on_a_read_only_root(service: str) -> None:
    spec = _services()[service]
    assert spec.get("read_only") is True
    assert any(v.startswith("plugin_state:") for v in spec["volumes"]), (
        "baselithbot writes its secret-store key under its own directory; "
        "read-only root needs that on a volume shared by api and worker"
    )


@pytest.mark.parametrize("service", ["api", "worker"])
def test_baselithbot_state_is_named_not_found_by_legacy_fallback(service: str) -> None:
    """The volume is the state dir by ``BASELITHBOT_STATE_DIR``, not by accident.

    Mounted at ``plugins/baselithbot/.state`` with no env, the plugin found it
    only through its deprecated in-package fallback and warned on every boot.
    The same named volume now mounts outside the package tree, so existing
    deployments keep their key and stores.
    """
    spec = _services()[service]
    env = dict(item.split("=", 1) for item in spec["environment"])
    state_dir = env["BASELITHBOT_STATE_DIR"]
    assert f"plugin_state:{state_dir}" in [
        v.split("#")[0].strip() for v in spec["volumes"]
    ]
    assert "/plugins/" not in state_dir


def test_every_service_rotates_logs_and_caps_tasks() -> None:
    for name, spec in _services().items():
        assert spec.get("logging", {}).get("options", {}).get("max-size"), name
        assert (
            spec.get("deploy", {}).get("resources", {}).get("limits", {}).get("pids")
        ), name


def test_worker_does_not_inherit_the_http_healthcheck() -> None:
    assert _services()["worker"]["healthcheck"] == {"disable": True}


def test_api_start_period_matches_the_image() -> None:
    health = _services()["api"]["healthcheck"]
    assert health["start_period"] == "300s"
    assert health["start_interval"] == "5s"
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "--start-period=300s" in text


def test_gateway_publishes_ipv4_only_and_probes_itself() -> None:
    gateway = _services()["gateway"]
    assert gateway["ports"] == ["0.0.0.0:80:80"]
    assert "__gateway/healthz" in " ".join(gateway["healthcheck"]["test"])


def test_tei_profile_mirrors_the_chart_defaults() -> None:
    chart = yaml.safe_load(
        (REPO_ROOT / "deploy/helm/baselithcore/values.yaml").read_text()
    )["inference"]["tei"]
    services = _services()
    for name, kind in (("tei-embed", "embedding"), ("tei-rerank", "rerank")):
        spec = services[name]
        assert spec["profiles"] == ["inference"]
        assert (
            spec["image"] == f"{chart['image']['repository']}:{chart['image']['tag']}"
        )
        assert chart[kind]["model"] in spec["command"]
        for arg in chart[kind]["args"]:
            assert arg in spec["command"]
        assert spec["read_only"] is True


# --- image and release ------------------------------------------------------


def test_runtime_stage_never_consults_the_hub() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime = text[text.index("AS runtime") :]
    assert re.search(r"^\s*HF_HUB_OFFLINE=1", runtime, flags=re.M), (
        "the baked model cache is re-checked against the Hub on every load"
    )


def test_env_files_are_excluded_from_every_copied_tree() -> None:
    rules = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert "**/.env" in rules and "**/.env.*" in rules
    assert "!.env.example" in rules


def test_release_signs_recursively_and_checks_the_index_it_built() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "cosign sign --recursive" in text
    # The index digest is read back through a mutable tag, so the step must
    # verify the index is exactly what this run's two build legs pushed. Each
    # leg pushes an image index (image + attestation manifest) and
    # `imagetools create` flattens its sources: the merged index lists the
    # legs' children, never the legs' own digests, so the check compares
    # against the children (v0.42.0 shipped unsigned because it did not).
    assert "imagetools inspect --raw" in text
    assert ".manifests[].digest" in text, (
        "the expected set must be built from each leg's children"
    )
    assert '"${EXPECTED}" != "${ACTUAL}"' in text, (
        "the merged index must hold exactly the legs' children"
    )
    assert "Manifest.Manifests}}{{.Digest}}" not in text, (
        "comparing leg digests with the flattened index can never pass"
    )
