---
name: verify-done
description: Use before claiming any change works, is done, fixed, or verified — especially API, plugin or UI changes, and above all when a build passed, a test went green, or an endpoint answered and that feels like proof.
---

# Verifying "done" in this repo

Four oracles here **lie**. Each has produced a confident wrong conclusion. Never claim done from them alone.

## The lying oracles

| Looks like proof | Why it lies | Real check |
|---|---|---|
| `/openapi.json` shows or omits a route | [core/api/factory.py](../../../core/api/factory.py) sets `openapi_url=None` when docs are off — zero paths while every route works | Probe over HTTP, assert status codes, and include a deliberately nonexistent path as a control |
| `TestClient(create_app())` responds | Plugin routers mount during the **async lifespan**, via `PluginRuntimeHooks`. A bare call sees only the routers `create_app()` includes directly — and `/health` itself lives in the `api_routers` plugin, so it is absent | Use `TestClient` as a context manager (`with TestClient(app) as c:`) so lifespan runs |
| Port 8000 answers | 8000 is the default (`core/config/app.py`), so it is usually a foreign dev server from another project under `~/dev/personale/baselith` — not ours to kill | Start the backend on a free port and poll `/health` for 200; boot loads models, so a slow start is not a crash |
| `npm run build` green | Not proof the right code shipped | Grep the built asset under `plugins/baselithbot/ui/dist/` for a string only the new code introduces |

Also: **a SKIPPED integration test is not a pass.** `tests/conftest.py` replaces `psycopg` at import time with a `MagicMock` whose cursor happily "executes" any SQL. `BASELITH_TEST_REAL_DB` is the opt-in escape hatch, and CI never sets it — the `python_test` job even starts a Postgres service container that the mocked tests never touch. That green is the green of tests that never ran.

## Infra precheck (before any E2E)

`python backend.py` blocks in lifespan waiting for Postgres on `127.0.0.1:5432` (see [docker-compose.yml](../../../docker-compose.yml)). The Docker daemon is often off on this machine.

```bash
docker info >/dev/null 2>&1 || echo "DOCKER DOWN"
```

Docker down means: do **not** silently start Docker Desktop. Report verification as **blocked**, or ask. Partial check that works without infra: static mounts are visible in-process — inspect `app.routes` from `create_app()` — but API routers need lifespan.

## Verification ladder

1. Gates: use the `run-gates` skill.
2. DB-touching tests for real: `BASELITH_TEST_REAL_DB=1 python -m pytest tests/integration/<area> -q -p no:cacheprovider --no-cov` — confirm they RAN, not skipped.
3. API surface change: also re-export both OpenAPI specs and diff them, since the `openapi_drift` job gates on it.
4. Backend E2E (needs Docker): start the backend on a **free port**, wait for `/health` 200, then curl the changed surface and assert status codes against a nonexistent-path control.
5. UI change: rebuild, grep the built asset for the new string, restart the backend (mounts are construction-time), hard-refresh. A missing `ui/dist` must render the self-diagnosing placeholder, not an opaque 404.
6. `core/` change: not done until the `mirror-to-enterprise` skill has run and the enterprise gates are green **there**.
7. Report honestly: what was verified, what was blocked and why. "Blocked on Docker" is a valid outcome; "should work" is not a verification.

## Red flags — you are about to lie to yourself

- "openapi.json shows it, so it's live" / "openapi.json lacks it, so it's broken"
- "The test suite is green" — were the integration tests skipped?
- "Port 8000 answered" — whose server?
- "The build passed, so the fix shipped"
- "The server was already running, no restart needed" — plugin mounts happen at construction and during lifespan; dropping new files next to a running process does nothing
