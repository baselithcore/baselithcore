---
name: new-plugin
description: Use before writing any new feature that is domain-specific, an external integration, or a business capability — the Sacred Core rule routes all of it into plugins/, and this sequences the compliant scaffold, signing and verification.
---

# Creating a new plugin

The Sacred Core rule in the root [CLAUDE.md](../../../CLAUDE.md) is an architectural invariant, not a preference: `core/` holds only domain-agnostic orchestration, infrastructure and utilities. Anything domain-specific, any external integration, any business feature lives under `plugins/<name>/`. [scripts/check_architecture_boundaries.py](../../../scripts/check_architecture_boundaries.py) enforces it — new files under the frozen `core/agents|doc_sources|goals|routers|scraper` prefixes are rejected, and so is any new `core -> plugins` import.

## Phase 0 — Decide before writing code

1. **Is this really a plugin?** Yes if it names a domain, a vendor, or a business rule. No if it is a generic runtime primitive — that belongs in `core/`, outside the frozen prefixes.
2. **Does `core/` already carry it?** Survey the subsystem map in [mkdocs-site/docs/architecture/agentic-patterns.md](../../../mkdocs-site/docs/architecture/agentic-patterns.md) before building anything the orchestrator, memory, resilience or model layers already provide. Record what you reused and why you skipped the near misses — it goes in the PR description.
3. **Dependency direction.** A plugin imports `core`; `core` never imports the plugin. Need core to call into you? Add a seam in `core/` (registry, protocol, event) and register from the plugin side.
4. **Does it need a UI?** React + Vite only, built output only (`ui/dist/**` ships; `ui/src/` and `ui/node_modules/` are excluded in `[tool.setuptools.exclude-package-data]`).

## Phase 1 — Scaffold

Reference structure: [plugins/example-plugin/](../../../plugins/example-plugin/) — `plugin.py`, `manifest.yaml`, `router.py`, `handlers.py`, `models.py`, `persistence.py`, `utils.py`, `skills/`, `tests/`.

- **Every file under 500 lines from the first commit.** Design the module split now; "split later" is how the ratchet gets a new entry, and a new entry is a regression.
- **`manifest.yaml`**: `name` == directory name. Fill `version` (start `0.1.0`), `description`, `author`, `tags`, `python_dependencies`, `plugin_dependencies` (keyed by **registry name**). The manifest filename must be one of `manifest.yaml|yml|json` — those are the names declared in `[tool.setuptools.package-data]`.
- **`__init__.py` with explicit exports** in every module (repo convention).
- **Secrets are `SecretStr`** — `pydantic.SecretStr`, or `set[SecretStr]` for collections. A plain `str` API key leaks through `repr()` and Sentry frames and is rejected at review.
- **HTTP middleware is pure ASGI** (`async def __call__(scope, receive, send)`), never `BaseHTTPMiddleware`.
- **Tool/skill returns** use `SkillResult` with `ok` / `fail` / `partial` from [core/plugins/result.py](../../../core/plugins/result.py). Declarative catalogs go in `SKILL.md` files under the plugin, loaded by `DeclarativeSkillLoader` ([core/plugins/declarative.py](../../../core/plugins/declarative.py)).
- **Google-style docstrings** on the public API; mock LLMs and DBs in the plugin's unit tests.

## Phase 2 — Sign

The first integrity signature is manual; the `sign-changed-plugins` pre-commit hook only re-signs plugins that already declare `integrity_sha256`.

```bash
baselith plugin sign plugins/<name>                    # integrity_sha256 only
python scripts/sign_plugin_ed25519.py sign plugins/<name> --key-env SIGNING_KEY
python scripts/check_plugin_integrity.py plugins/<name>
```

The Ed25519 form also writes `signature_ed25519`, required when the deployment
sets `BASELITH_REQUIRE_PLUGIN_SIGNATURES`; the private key is read from the
named environment variable, never from argv.

The hook never bumps the manifest `version` — that stays manual, in the same commit as the change.

## Phase 3 — Verify

```bash
python scripts/check_architecture_boundaries.py
python scripts/check_file_size.py
python scripts/check_plugin_integrity.py plugins/<name>
python -m pytest tests/plugins/<name> -q --no-cov
ruff check plugins/<name>
```

Adding the plugin to `OFFICIAL_PLUGIN_DIRS` in [scripts/check_official_plugin_typing.py](../../../scripts/check_official_plugin_typing.py) enrolls it in the strict typing gate — do that only when the plugin is genuinely official, and make it pass rather than trimming the list.

Docs ship with the code: add or update the page under [mkdocs-site/docs/plugins/](../../../mkdocs-site/docs/plugins/) in the same change.

## Red flags

- A new file under `core/agents/`, `core/doc_sources/`, `core/goals/`, `core/routers/` or `core/scraper/` — those prefixes are frozen. Create a plugin.
- Extending `LEGACY_CORE_FILE_ALLOWLIST` or grandfathering a new `core -> plugins` shim to make the boundary check pass.
- Adding an entry to `scripts/file_size_baseline.json` for a file you just wrote. The baseline is empty and only shrinks.
- A credential typed as `str`.
- Hand-editing `integrity_sha256`.
- Merging the code without its `mkdocs-site/docs/` page.
