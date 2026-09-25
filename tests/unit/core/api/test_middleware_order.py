"""Regression test for the security-relevant middleware ordering in create_app.

The perimeter guards (Host validation + CSRF Origin) must run *outer* to the
expensive/stateful layers (Quota, Idempotency, Tenant, plugin layers) so a
spoofed-Host or CSRF-failing request is rejected by a cheap header compare
before it can consume a quota unit or take an Idempotency lock — yet *inner* to
SecurityHeaders and RequestSizeLimit so their 400/403s still carry CSP/HSTS and
oversized bodies are rejected first, and inner to CORS so a browser can read
those rejections at all.

Built in a fresh subprocess (like test_api_versioning) so the result cannot
depend on collection order, and ``create_app`` runs its real boot path. Auth is
disabled and a trusted host is set purely so the config validates and the
optional TrustedHostMiddleware is present.
"""

import json
import os
import pathlib
import subprocess
import sys

_CHILD = r"""
from core.api.factory import create_app

app = create_app()
# user_middleware[0] is the OUTERMOST layer (add_middleware inserts at index 0).
names = [m.cls.__name__ for m in app.user_middleware]
cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
static = next(r for r in app.routes if getattr(r, "name", None) == "static")

# A real OPTIONS preflight through the assembled stack. ASGITransport drives the
# app directly, so no lifespan (and no DB/Redis) is started.
import asyncio

import httpx


async def _preflight():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://example.com") as c:
        res = await c.options(
            "/chat",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
                # Every header a first-party browser client sends: a single
                # one missing from allow_headers turns the preflight into 400.
                "Access-Control-Request-Headers": (
                    "Content-Type, X-API-Key, Mcp-Session-Id, "
                    "Mcp-Protocol-Version, Mcp-Method, Mcp-Name, Last-Event-ID"
                ),
            },
        )
    return res.status_code, {k.lower(): v for k, v in res.headers.items()}


preflight_status, preflight_headers = asyncio.run(_preflight())

import json
print("===ORDER_BEGIN===")
print(
    json.dumps(
        {
            "names": names,
            "cors_expose": list(cors.kwargs.get("expose_headers", [])),
            "static_dir": str(static.app.directory),
            "preflight_status": preflight_status,
            "preflight_headers": preflight_headers,
        }
    )
)
print("===ORDER_END===")
"""


_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[4])


def _app_snapshot(cwd: str | None = None) -> dict:
    """Build the real app in a fresh interpreter and report its HTTP surface.

    ``cwd`` lets a test build the app from a *different* working directory,
    which is how the static-mount anchor is pinned: a relative directory only
    resolves when the process happens to start at the repo root.
    """
    env = os.environ.copy()
    env.update(
        {
            "AUTH_REQUIRED": "false",
            "TRUSTED_HOSTS": '["example.com"]',
            "ALLOW_ORIGINS": '["https://app.example.com"]',
            # The child may run from elsewhere; keep the package importable.
            "PYTHONPATH": _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", ""),
        }
    )
    out = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or _REPO_ROOT,
        check=True,
    ).stdout
    payload = out.split("===ORDER_BEGIN===")[1].split("===ORDER_END===")[0].strip()
    result: dict = json.loads(payload)
    return result


def _middleware_order() -> list[str]:
    names: list[str] = _app_snapshot()["names"]
    return names


def test_perimeter_guards_outer_to_stateful_layers():
    names = _middleware_order()

    def idx(name: str) -> int:
        assert name in names, f"{name} not registered ({names})"
        return names.index(name)  # lower index = more outer

    # Perimeter guards run outer to the expensive/stateful layers…
    for inner in ("QuotaMiddleware", "IdempotencyMiddleware", "TenantMiddleware"):
        assert idx("CSRFOriginMiddleware") < idx(inner)
        assert idx("TrustedHostMiddleware") < idx(inner)

    # …TrustedHost outermost of the two guards…
    assert idx("TrustedHostMiddleware") < idx("CSRFOriginMiddleware")

    # …but both inner to size-limiting and security headers, so oversized
    # bodies are rejected first and guard 400/403s still carry CSP/HSTS.
    assert idx("RequestSizeLimitMiddleware") < idx("TrustedHostMiddleware")
    assert idx("SecurityHeadersMiddleware") < idx("TrustedHostMiddleware")


#: Outermost -> innermost. Each layer is here for a reason the next one cannot
#: serve: metrics must see the true end-to-end latency; every response needs a
#: request id; CSP/HSTS must reach even a preflight; CORS must wrap the guards
#: so a browser can read their rejections; and the guards themselves run cheap
#: header compares before anything stateful.
EXPECTED_PERIMETER = [
    "HTTPMetricsMiddleware",
    "RequestIdMiddleware",
    "SecurityHeadersMiddleware",
    "CORSMiddleware",
    "RequestSizeLimitMiddleware",
    "TrustedHostMiddleware",
    "CSRFOriginMiddleware",
]


def test_the_perimeter_sequence_is_exactly_pinned():
    """The whole outer stack, in order — not just pairwise constraints."""
    names = _middleware_order()
    actual = [name for name in names if name in set(EXPECTED_PERIMETER)]

    assert actual == EXPECTED_PERIMETER, f"perimeter order drifted: {names}"


def test_cors_wraps_the_guards_so_browsers_can_read_rejections():
    """CORS must wrap the guards, not hide behind them.

    Registered inner to RequestSizeLimit/TrustedHost/CSRF, a guard rejection
    (400/403/413) leaves the response without ``Access-Control-Allow-Origin``,
    so a browser reports an opaque CORS failure instead of the real status the
    API chose. Preflights are answered before the guards for the same reason.
    """
    names = _middleware_order()

    def idx(name: str) -> int:
        assert name in names, f"{name} not registered ({names})"
        return names.index(name)  # lower index = more outer

    for inner in (
        "RequestSizeLimitMiddleware",
        "TrustedHostMiddleware",
        "CSRFOriginMiddleware",
    ):
        assert idx("CORSMiddleware") < idx(inner), (
            f"CORSMiddleware must be outer to {inner} ({names})"
        )

    # …yet inner to SecurityHeaders and the observability layers, so every
    # response — preflights included — carries CSP/HSTS and an X-Request-ID.
    assert idx("SecurityHeadersMiddleware") < idx("CORSMiddleware")
    assert idx("RequestIdMiddleware") < idx("CORSMiddleware")
    assert idx("HTTPMetricsMiddleware") < idx("CORSMiddleware")


def test_a_preflight_response_carries_the_security_headers():
    """CORS answers preflights itself; SecurityHeaders must still see them.

    This is the ordering constraint that a name-index assertion cannot prove:
    only a response that actually travelled the stack shows whether the layer
    that short-circuits sits inside the layer that stamps.
    """
    snapshot = _app_snapshot()
    headers = snapshot["preflight_headers"]

    assert snapshot["preflight_status"] in (200, 204), snapshot["preflight_status"]
    assert headers.get("access-control-allow-origin") == "https://app.example.com"
    assert "content-security-policy" in headers, headers
    assert headers.get("x-content-type-options") == "nosniff"
    # RequestId is outermost of the three, so it stamps the preflight too.
    assert "x-request-id" in headers, headers


def test_request_id_is_exposed_to_browsers():
    """``X-Request-ID`` is useless to a browser client unless CORS exposes it."""
    assert "X-Request-ID" in _app_snapshot()["cors_expose"]


def test_client_headers_pass_the_preflight():
    """The SDK's ``X-API-Key`` and the MCP/SSE headers must be allowed.

    The TypeScript SDK authenticates with ``x-api-key`` and an MCP browser
    client carries ``Mcp-Session-Id``/``MCP-Protocol-Version``; without them in
    ``allow_headers`` the preflight fails before authentication even runs.
    """
    snapshot = _app_snapshot()
    allowed = {
        h.strip().lower()
        for h in snapshot["preflight_headers"]["access-control-allow-headers"].split(
            ","
        )
    }

    assert snapshot["preflight_status"] == 200, snapshot["preflight_status"]
    for header in (
        "x-api-key",
        "mcp-session-id",
        "mcp-protocol-version",
        "mcp-method",
        "mcp-name",
        "last-event-id",
    ):
        assert header in allowed, (header, sorted(allowed))


def test_mcp_session_id_is_exposed_to_browsers():
    """An MCP browser client reads its session id off the initialize response."""
    assert "Mcp-Session-Id" in _app_snapshot()["cors_expose"]


def test_static_mount_is_anchored_to_the_package(tmp_path):
    """The static mount must not depend on the process working directory."""
    from_root = _app_snapshot()["static_dir"]
    from_elsewhere = _app_snapshot(cwd=str(tmp_path))["static_dir"]

    assert os.path.isabs(from_elsewhere), from_elsewhere
    assert os.path.realpath(from_elsewhere) == os.path.realpath(from_root)
    assert os.path.isdir(from_elsewhere)
