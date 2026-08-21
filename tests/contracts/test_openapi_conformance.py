"""Runtime OpenAPI conformance (schemathesis).

The CI OpenAPI drift gate proves the exported spec matches the code's
*declared* schema; it never checks that live responses conform to it. This
suite fuzzes the unauthenticated read-only surface through the ASGI app and
validates every response (status, content-type, body schema) against the
spec — server errors and undocumented responses fail.

Scope is deliberately narrow (no-auth GET endpoints): authenticated and
side-effecting operations need seeded credentials/state and belong to an
integration environment, not this hermetic gate.
"""

import os

import pytest
import schemathesis
from hypothesis import HealthCheck, settings

pytestmark = pytest.mark.contract


class _NoLifespan:
    """ASGI wrapper that acks lifespan without running it.

    The real lifespan needs live infrastructure (plugin activation, DB/Redis
    pools) — this gate is hermetic, so it exercises only endpoints that do not
    depend on lifespan-initialized state (see the include/exclude filters).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan":
            await self.app(scope, receive, send)
            return
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


def _build_schema():
    """Build the app + schema under a hermetic env, leaving no trace.

    The env overrides and the config singletons the app build caches are
    rolled back afterwards — module import must not leak state into unrelated
    tests (pytest-randomly interleaves modules in arbitrary order).
    """
    overrides = {
        "POSTGRES_ENABLED": "false",
        # The factory auto-disables /openapi.json in prod-like setups; the
        # schema is the object under test here, so force it on.
        "DOCS_ENABLED": "true",
    }
    saved = {key: os.environ.get(key) for key in overrides}
    for key, value in overrides.items():
        os.environ[key] = value
    try:
        from core.api.factory import create_app

        return schemathesis.openapi.from_asgi(
            "/openapi.json", _NoLifespan(create_app())
        )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        # Reset every lazily-cached `_*_config` singleton in core.config so
        # later tests re-read the restored environment.
        import importlib
        import pkgutil

        import core.config as config_pkg

        for info in pkgutil.iter_modules(config_pkg.__path__):
            module = importlib.import_module(f"core.config.{info.name}")
            for attr in list(vars(module)):
                if attr.startswith("_") and attr.endswith("_config"):
                    setattr(module, attr, None)


# /health/ready legitimately answers 200 or 503 depending on infrastructure
# availability — in this hermetic environment it is always 503, so it cannot
# be asserted here (the 503 IS documented in the spec). /console is the SPA
# catch-all: its responses depend on built frontend assets being present,
# which is a packaging concern, not an API contract.
# /metrics is admin-basic-auth-gated by default, so this hermetic suite only
# ever sees 401 (skipped) — no conformance value, while its multiprocess env
# sensitivity makes it order-fragile under random test interleaving.
schema = (
    _build_schema()
    .include(method="GET")
    .exclude(path_regex=r"/(admin|static|ui|console)|/health/ready$|/metrics$")
)


@schema.parametrize()
@settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_read_only_surface_conforms_to_spec(case):
    response = case.call()
    if response.status_code in (401, 403):
        # Auth-gated operation: exercising it needs seeded credentials, which
        # is out of scope for this hermetic gate.
        return
    from schemathesis.specs.openapi.checks import allow_header_conformance

    # 5xx on a documented read-only endpoint is always a conformance bug;
    # everything else must match the spec (status documented, body validates).
    # Allow-header composition on 405s is Starlette's router, not this API's
    # contract — excluded rather than patched around framework internals.
    case.validate_response(response, excluded_checks=(allow_header_conformance,))
