"""Regression test for the security-relevant middleware ordering in create_app.

The perimeter guards (Host validation + CSRF Origin) must run *outer* to the
expensive/stateful layers (Quota, Idempotency, CORS, plugin layers) so a
spoofed-Host or CSRF-failing request is rejected by a cheap header compare
before it can consume a quota unit or take an Idempotency lock — yet *inner* to
SecurityHeaders and RequestSizeLimit so their 400/403s still carry CSP/HSTS and
oversized bodies are rejected first.

Built in a fresh subprocess (like test_api_versioning) so the result cannot
depend on collection order, and ``create_app`` runs its real boot path. Auth is
disabled and a trusted host is set purely so the config validates and the
optional TrustedHostMiddleware is present.
"""

import json
import os
import subprocess
import sys

_CHILD = r"""
from core.api.factory import create_app

app = create_app()
# user_middleware[0] is the OUTERMOST layer (add_middleware inserts at index 0).
names = [m.cls.__name__ for m in app.user_middleware]
import json
print("===ORDER_BEGIN===")
print(json.dumps(names))
print("===ORDER_END===")
"""


def _middleware_order() -> list[str]:
    env = os.environ.copy()
    env.update(
        {
            "AUTH_REQUIRED": "false",
            "TRUSTED_HOSTS": '["example.com"]',
        }
    )
    out = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout
    payload = out.split("===ORDER_BEGIN===")[1].split("===ORDER_END===")[0].strip()
    return json.loads(payload)


def test_perimeter_guards_outer_to_stateful_layers():
    names = _middleware_order()

    def idx(name: str) -> int:
        assert name in names, f"{name} not registered ({names})"
        return names.index(name)  # lower index = more outer

    # Perimeter guards run outer to the expensive/stateful layers…
    for inner in ("QuotaMiddleware", "IdempotencyMiddleware", "CORSMiddleware"):
        assert idx("CSRFOriginMiddleware") < idx(inner)
        assert idx("TrustedHostMiddleware") < idx(inner)

    # …TrustedHost outermost of the two guards…
    assert idx("TrustedHostMiddleware") < idx("CSRFOriginMiddleware")

    # …but both inner to size-limiting and security headers, so oversized
    # bodies are rejected first and guard 400/403s still carry CSP/HSTS.
    assert idx("RequestSizeLimitMiddleware") < idx("TrustedHostMiddleware")
    assert idx("SecurityHeadersMiddleware") < idx("TrustedHostMiddleware")
