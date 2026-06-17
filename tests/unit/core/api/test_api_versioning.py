"""Tests for the additive /v1 API versioning aliases.

Route presence is checked via the OpenAPI schema (``app.openapi()["paths"]``)
rather than by iterating ``app.routes``. FastAPI 0.137+ uses *lazy* router
inclusion: ``include_router`` stores ``_IncludedRouter`` placeholders in
``app.routes`` that are only resolved to concrete sub-routes at request time, so
iterating ``app.routes`` no longer yields the flattened ``/health`` / ``/v1/*``
paths (this is what broke the assertions on CI, which runs a newer FastAPI than
some dev machines). The OpenAPI schema resolves the full path set consistently
across FastAPI versions and reflects what the app actually serves.

The app is additionally built in a **fresh subprocess** — a pristine interpreter
where no other test has run — so the result cannot depend on collection order or
xdist worker layout, and the production import/boot path is exercised cleanly.
"""

import json
import os
import subprocess
import sys

# Run in the child: build the app and print its OpenAPI paths as JSON between
# markers so the parent can parse past any import-time log noise. ``openapi()``
# is pure (no lifespan/startup), so this needs no DB/Redis and no heavy extras.
_CHILD = r"""
import json
from core.api.factory import create_app

app = create_app()
paths = sorted(app.openapi().get("paths", {}).keys())
print("===PATHS_BEGIN===")
print(json.dumps(paths))
print("===PATHS_END===")
"""


def _app_paths(**env_overrides: str) -> set[str]:
    """Build ``create_app()`` in a clean subprocess and return its OpenAPI paths."""
    env = os.environ.copy()
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"app build subprocess failed (rc={proc.returncode}).\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    out = proc.stdout
    try:
        payload = out.split("===PATHS_BEGIN===", 1)[1].split("===PATHS_END===", 1)[0]
        return set(json.loads(payload.strip()))
    except (IndexError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse paths from subprocess output: {exc}\n"
            f"--- stdout ---\n{out}\n--- stderr ---\n{proc.stderr}"
        ) from exc


def test_v1_aliases_present_by_default():
    paths = _app_paths()
    v1_paths = {p for p in paths if p.startswith("/v1/")}
    # At least the status health route should be mirrored under /v1.
    assert "/v1/health" in v1_paths, sorted(v1_paths)[:20]


def test_unprefixed_routes_still_present():
    # Versioning is additive — original paths must remain.
    paths = _app_paths()
    assert "/health" in paths


def test_v1_can_be_disabled():
    paths = _app_paths(API_V1_ENABLED="false")
    assert not any(p.startswith("/v1/") for p in paths)
    # Unprefixed still there.
    assert "/health" in paths
