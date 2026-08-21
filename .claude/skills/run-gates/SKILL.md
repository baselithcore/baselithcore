---
name: run-gates
description: Use before pushing, opening a PR, or claiming the gates are green — runs the repo quality battery (lint, boundaries, file size, typing, plugin integrity, tests) in fast-fail order and says how to fix each failure without cheating it.
---

# Running the gate battery

Fast-fail order: cheap static gates first, tests last. Tool versions must match CI — ruff `0.15.5`, mypy `2.3.0`; the pins live in [.github/workflows/ci.yml](../../../.github/workflows/ci.yml) and [.pre-commit-config.yaml](../../../.pre-commit-config.yaml) and must stay in lockstep.

```bash
ruff check . --exclude templates,examples
ruff format --check . --exclude templates,examples
python scripts/check_architecture_boundaries.py     # Sacred Core rule
python scripts/check_file_size.py                   # 500-line ratchet
mypy core --ignore-missing-imports --incremental --fast-module-lookup
python scripts/check_official_plugin_typing.py      # strict typing, official plugins
python scripts/check_core_resilience_typing.py
python -m pytest                                    # asyncio_mode=auto, --cov-fail-under=75
```

Touched a plugin's `.py`? Also:

```bash
python scripts/check_plugin_integrity.py plugins/<name>
```

Touched the API surface? The `openapi_drift` CI job diffs the committed spec against a fresh export:

```bash
python scripts/export_openapi.py
python scripts/export_openapi.py mkdocs-site/docs/api/specs/openapi.json
git diff --exit-code sdk/openapi.json mkdocs-site/docs/api/specs/openapi.json
```

Changed dependency ranges in `pyproject.toml`? `uv lock` — the `uv_lock_check` job fails on a stale lock.

## Interpreting failures

| Gate fails | Correct fix | NEVER |
|---|---|---|
| ruff | Fix the reported rule | `# noqa` |
| mypy / official-plugin typing / core-resilience typing | Fix the actual type | `# type: ignore`, widening to `Any`, or trimming the gate's directory list |
| file-size (new file over 500) | Split it — use the `split-file` skill | Add a `file_size_baseline.json` entry |
| file-size (baselined file grew) | Shrink it back; baselined files may only shrink | Bump its baseline count |
| architecture boundaries (new file under a frozen `core/` prefix) | Create a plugin instead — use the `new-plugin` skill | Extend `LEGACY_CORE_FILE_ALLOWLIST` |
| architecture boundaries (`core -> plugins` import) | Invert the dependency: seam in `core/`, consumer in the plugin | Grandfather a new shim |
| plugin integrity | Commit normally — the `sign-changed-plugins` pre-commit hook re-signs any plugin that already declares `integrity_sha256`. Manual `baselith plugin sign plugins/<name>` only for the first signature or after a `--no-verify` commit. The hook does NOT bump the manifest `version` — that stays manual, same commit | Hand-edit `integrity_sha256` |
| openapi drift | Re-export both specs from the changed routers/models | Hand-edit the JSON |
| pytest green but the change touches Postgres | The integration tests were almost certainly SKIPPED — `tests/conftest.py` mocks `psycopg` at import. Re-run with `BASELITH_TEST_REAL_DB=1 python -m pytest tests/integration/<area> -q --no-cov` and confirm they ran | Trust green for DB paths |
| coverage under 75 | Add the missing tests | Lower `--cov-fail-under` |

The coverage floor is a ratchet: raise it as coverage grows, never lower it.

After legitimately splitting a baselined over-cap file: `python scripts/check_file_size.py --update-baseline` — it deletes the entry once the file is back under the cap.

Change touched `core/`? Gates green here is not the end — the `mirror-to-enterprise` skill applies.
