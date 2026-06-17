"""Tests for the additive /v1 API versioning aliases.

These assertions build the FastAPI app via ``create_app()`` and inspect its
route table. ``create_app()`` mounts module-level router singletons from
``plugins.api_routers`` (via the ``core.routers`` shims) and ``core.api.factory``
binds them at import time. When the whole suite runs in a shared interpreter
(notably under ``pytest -n auto`` on CI), another test in the same worker can
leave those singletons mutated, producing an app missing routes — which made an
in-process build of the app flaky in a way that was not reproducible locally.

To make these tests deterministic regardless of collection order or xdist
worker layout, the app is built in a **fresh subprocess**: a pristine
interpreter where no other test has run, so global module state cannot be
polluted. This also exercises the real production import/boot path.
"""

import json
import os
import subprocess
import sys

# Run in the child: build the app and print its route paths as JSON between
# markers so the parent can parse past any import-time log noise.
_CHILD = r"""
import json
from core.api.factory import create_app

app = create_app()
paths = sorted({getattr(r, "path", "") for r in app.routes})
print("===PATHS_BEGIN===")
print(json.dumps(paths))
print("===PATHS_END===")
"""


def _app_paths(**env_overrides: str) -> set[str]:
    """Build ``create_app()`` in a clean subprocess and return its route paths."""
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
            f"could not parse route paths from subprocess output: {exc}\n"
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
