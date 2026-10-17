# Plugin repository layout

Every directory under `baselith/plugins/` follows one layout, so a reader who
knows one plugin knows them all and a plugin extracted into its own repository
looks like a project from day one. The gate
`scripts/check_plugin_layout.py` enforces it on every commit (pre-commit hook
`plugin-layout`).

## The root

Only these entries may sit at `baselith/plugins/<name>/`:

| Entry | Role |
| --- | --- |
| `manifest.yaml` | identity, version, dependencies, `frontend:` build contract, `integrity_sha256` |
| `README.md` | what the plugin is, how to run it, where the docs are |
| `__init__.py`, `plugin.py` | the package and the plugin class the loader instantiates |
| `LICENSE`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md` | GitHub community files |
| `.gitignore`, `.env.example`, `.editorconfig`, `.pre-commit-config.yaml`, `mkdocs.yml`, `pyproject.toml`, `requirements.txt`, `alembic.ini`, `py.typed` | tool configuration read from the project root by the tool that owns it |
| `DESIGN.md`, `PRODUCT.md` | design-tooling contracts read from the project root |
| skeleton modules | `config.py`, `models.py`, `events.py`, `errors.py`, `i18n.py`, `security.py`, `tenancy.py`, `dependencies.py`, `router.py`, `handlers.py`, `agent.py`, `persistence.py`, `memory.py`, `utils.py`, `metrics.py`, `audit.py`, `compliance.py`, `privacy_provider.py`, `health.py`, `cli.py`, `mcp_tools.py`, `ratelimit.py`, `app_factory.py`, `context_bridge.py`, `_bootstrap.py`, and the agent-plugin seams `types.py`, `schemas.py`, `prompts.py`, `tools.py`, `llm.py`, `service.py` |
| directories | any sub-package or named directory (`docs/`, `tests/`, `ui/`, `static/`, `assets/`, `scripts/`, `locales/`, `migrations/`, `vendor/`, `.github/`, …) |

Everything else is a **loose root file** and fails the gate:

- a Markdown page that is not a community file → under `docs/` and in the
  `mkdocs.yml` nav (a superseded working note is deleted, not archived);
- a Python module outside the skeleton → inside a sub-package named for its
  concern (`routes/`, `sources/`, `readers/`, `engine/`, `runner/`, …). A
  module that grows siblings becomes a package: `agent.py` →
  `agent/__init__.py` with `agent/actions.py` beside it, so the import path
  `…agent` survives;
- SQL, YAML examples, data files → a named directory (`db/`, `examples/`,
  `data/`).

## Everywhere in the tree

- No tracked build output, cache or runtime state: `__pycache__/`,
  `.ruff_cache/`, `node_modules/`, `.state/`, `qdrant_data/`, `vaults/`,
  `var/`, `logs/` fail the gate wherever they appear. Runtime state lives
  under `baselith.core.plugins.data_dir("<name>")`.
- No tracked private key: `*.pem`, `*.key`, `ssh_host_*`, `id_*`. Keys are
  generated at first start into the data directory.

## The ratchet

`scripts/plugin_layout_baseline.json` freezes the loose root files that
predate the rule, per plugin. A baselined file is tolerated while it is still
loose; once it moves, its entry is stale and the gate fails until the baseline
is refreshed:

```bash
python scripts/check_plugin_layout.py                    # gate
python scripts/check_plugin_layout.py --update-baseline  # after moving files
```

Never add an entry by hand — a new loose file gets moved, not frozen.

## Moving modules

`scripts/move_plugin_modules.py` moves modules inside a plugin and rewrites
every reference:

```bash
python scripts/move_plugin_modules.py document_sources web.py=sources/web.py readers.py=readers/__init__.py
```

It `git mv`s the files, rewrites absolute and relative imports in every
tracked `.py` (re-rendering relative imports from the importing file's new
location), rewrites the dotted path and the file path in docs, YAML, TOML and
test patch targets, and prints any remaining mention of a renamed basename.
Run `ruff format` on the touched files and the plugin's tests afterwards; the
move is a patch `version` bump.

Two things the tool cannot see:

- **`__file__` arithmetic.** A module that computes a path from its own
  location (`Path(__file__).parent / "ui"`) gains one `.parent` per level it
  moved down. Grep the moved files for `__file__` before running the tests.
- **Public import paths.** A plugin's modules are reachable from outside —
  documented here, imported by downstream plugins, or exposed through a
  `baselith.core` compatibility package that *is* the plugin package under
  another name (`baselith.core.doc_sources`, `baselith.core.scraper`). Keep
  every old path importable with
  `baselith.core.plugins.compat_aliases.register_module_aliases` in the
  plugin's `__init__.py` (lazy, one module identity, listed names only — see
  [Plugins → Keeping old import paths alive](../core-modules/plugins.md#keeping-old-import-paths-alive));
  the move then stays a patch bump instead of a breaking change.
