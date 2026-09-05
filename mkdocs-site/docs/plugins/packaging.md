---
title: Plugin Packaging
description: Package plugins for distribution
---

<!-- markdownlint-disable-file MD046 -->

**Plugin Packaging** is the process of preparing a plugin for distribution. A well-structured package ensures reliable installation, safe updates, and compatibility with different framework versions.

!!! info "Why Package Plugins?"
    - **Distribution**: Share your plugin with other users
    - **Versioning**: Manage multiple versions systematically
    - **Dependencies**: Declare Python packages and plugin prerequisites in the manifest
    - **Validation**: `baselith plugin validate` checks syntax, manifest, dependencies and environment before you publish

---

## Package Structure

A plugin package requires a well-defined structure:

```text
my-plugin-1.0.0/
├── __init__.py          # Package marker (REQUIRED for marketplace publish)
├── plugin.py            # Entry point (REQUIRED)
├── manifest.yaml        # Package metadata (REQUIRED; .yml or .json also accepted)
├── README.md            # Documentation (recommended)
├── CHANGELOG.md         # Version history (recommended)
├── agent.py             # Agent implementation (if agent plugin)
├── handlers.py          # Flow handlers (if applicable)
├── static/              # Frontend assets (if UI plugin)
│   ├── main.js
│   └── styles.css
└── tests/               # Test suite (recommended)
    ├── __init__.py
    ├── test_plugin.py
    └── test_handlers.py
```

### Required Files

| File            | Purpose                                | Validation                                                                                   |
| --------------- | -------------------------------------- | -------------------------------------------------------------------------------------------- |
| `plugin.py`     | Entry point, Plugin class              | Must contain a class extending `Plugin`, `AgentPlugin`, `RouterPlugin` or `GraphPlugin`      |
| `manifest.yaml` | Identity, metadata, dependencies       | `name`, `version` and `description` present; parses as YAML (or JSON for `manifest.json`)   |
| `__init__.py`   | Makes the plugin importable as a package | Required by the marketplace `PluginValidator` when publishing                              |

### Recommended Files

| File               | Purpose             | Benefit                         |
| ------------------ | ------------------- | ------------------------------- |
| `README.md`        | User documentation  | Shown by `baselith plugin info` when present |
| `CHANGELOG.md`     | Change history      | Users understand what changed   |
| `tests/`           | Test suite          | Increases confidence and rating |
| `python_dependencies` in `manifest.yaml` | Runtime dependencies | Declarative plugin installation |

---

## Manifest

The `manifest.yaml` file contains all plugin metadata:

```yaml title="manifest.yaml"
name: my-plugin
version: 1.0.0
description: Brief but informative plugin description
author: Your Name
homepage: https://github.com/you/my-plugin
license: MIT
tags:
  - utility
  - helper
category: utility
min_core_version: 0.29.0
python_dependencies:
  - httpx>=0.25,<1.0
  - pydantic>=2.0
plugin_dependencies:
  core-utilities: ^1.0.0
required_resources:
  - llm
optional_resources:
  - postgres
environment_variables:
  - MY_PLUGIN_API_KEY
integrity_sha256: 7c2a1b...e9f0   # Optional. SHA-256 of everything the plugin ships and runs (manifest excluded).
```

### Manifest Fields

| Field                   | Required | Description                                      |
| ----------------------- | -------- | ------------------------------------------------ |
| `name`                  | ✅        | Unique plugin name (lowercase, hyphen-separated) |
| `version`               | ✅        | SemVer version (e.g., "1.0.0")                   |
| `description`           | ✅        | Brief description                                |
| `author`                | ❌        | Author name or organization                      |
| `license`               | ❌        | License (MIT, Apache-2.0, GPL-3.0, etc.)         |
| `min_core_version`      | ❌        | Minimum BaselithCore version — full SemVer `MAJOR.MINOR.PATCH` (e.g. `0.29.0`) |
| `max_core_version`      | ❌        | Maximum BaselithCore version, same format        |
| `python_dependencies`   | ❌        | Pip-style (PEP 440) package requirements         |
| `plugin_dependencies`   | ❌        | Mapping of plugin name → version constraint      |
| `dependencies`          | ❌        | Legacy list of required plugin **names**; prefer `plugin_dependencies` |
| `required_resources`    | ❌        | Core resources needed by the plugin              |
| `optional_resources`    | ❌        | Optional resources used when available           |
| `environment_variables` | ❌        | Required environment variables                   |
| `integrity_sha256`      | ❌        | Hex SHA-256 over everything the plugin ships and runs — see [What is hashed](#integrity) for the exact surface. The manifest itself is **excluded**, so the publisher can inject this field after computing the hash without invalidating it. Verified before `exec_module`; mismatch refuses load. In production a plugin without this field is refused by default (fail-closed) unless `BASELITH_ALLOW_UNSIGNED_IN_PROD=true`; set `BASELITH_REQUIRE_SIGNED_PLUGINS=true` to reject unsigned plugins in every environment. Compute via `baselith plugin sign` or `core.plugins.integrity.compute_plugin_hash()`. |

The class in `plugin.py` carries no identity of its own: `name`, `version` and every other
field are read from the manifest next to it (`core/plugins/_metadata.py`).

### Dependencies

Specify dependencies with version ranges in `manifest.yaml`:

```yaml
python_dependencies:
  - httpx>=0.25,<1.0
  - pydantic>=2.0
  - numpy~=1.24.0
plugin_dependencies:
  base-plugin: ^1.0.0
  helper-plugin: ~1.2.3
```

Two different grammars apply:

- `python_dependencies` entries are standard pip requirement strings (PEP 440):
  bounded ranges (`>=1.0,<2.0`) and compatible-release specifiers (`~=1.24`) are fine.
- `min_core_version`, `max_core_version` and every `plugin_dependencies` constraint are
  parsed by `core/plugins/version.py`: a **full** `MAJOR.MINOR.PATCH` version with at most
  one operator — `==`, `!=`, `>`, `>=`, `<`, `<=`, `^` (same major) or `~` (same
  major.minor). `^1.0` or `>=1.0,<2.0` are rejected as invalid versions.

!!! note "Warn-only by default"
    Core-version bounds and `plugin_dependencies` are checked when the plugin loads
    (`core/plugins/load_gates.py`). Problems are logged as warnings and the plugin still
    loads unless `BASELITH_ENFORCE_PLUGIN_COMPAT=true` is set, in which case an
    incompatible plugin is skipped.

---

## Signing for Integrity {#integrity}

The framework verifies a plugin's `integrity_sha256` digest before importing any of its
code. Use `baselith plugin sign` to compute the digest over the plugin's executable
surface and write it into the manifest:

```bash
# Compute the digest and write it into manifest.(yaml|yml|json)
baselith plugin sign plugins/my-plugin

# Compute and print the digest without modifying the manifest
baselith plugin sign plugins/my-plugin --check
```

| Argument / Flag | Description                                                        |
| --------------- | ------------------------------------------------------------------ |
| `path`          | Path to the local plugin directory                                 |
| `--check`       | Print the computed hash without modifying the manifest             |

### What is hashed

The digest covers every file the plugin **ships** that also **executes** — on the
host or in the operator's browser. Each contributing file adds its POSIX-relative
path and its raw bytes to the digest, in sorted order, so the hash is reproducible
across platforms.

| Category                                        | Files                                                              |
| ----------------------------------------------- | ------------------------------------------------------------------ |
| Source                                          | `*.py`, `*.pyi`                                                    |
| Build and packaging (what `pip install` trusts) | `pyproject.toml`, `setup.cfg`, `MANIFEST.in`, `requirements*.txt`  |
| Prompt bodies (they reach the model)            | `SKILL.md`                                                         |
| Native modules and shell scripts                | `*.so`, `*.pyd`, `*.dylib`, `*.sh`                                 |
| Front-end assets served from the plugin origin  | `*.js`, `*.mjs`, `*.cjs`, `*.wasm`, `*.html`, `*.htm`, `*.svg`, `*.css` |

In practice the last row means `ui/dist/**` and `static/**`. `.svg` counts as
executable because a same-origin SVG opened top-level runs its embedded script,
and `.css` because it rewrites what the operator sees and clicks.

Excluded from the digest:

- The **manifest** itself — this is what lets `sign` write the hash back into it
  without invalidating it.
- `__pycache__/`, `.git/`, `node_modules/`.
- Everything under `ui/` **except** the compiled bundle `ui/dist/**`. `ui/src/`,
  `ui/node_modules/` and the tsconfig/vite build inputs never ship (mirroring
  `[tool.setuptools.exclude-package-data]` in the plugin's `pyproject.toml`), so
  they stay out.
- `*.json`, `*.ts`/`*.tsx`, images, and Markdown other than `SKILL.md`.

!!! warning "Re-sign after `npm run build`"
    Since 0.27 the compiled dashboard (`ui/dist/**`) is part of the hashed
    surface, so rebuilding a plugin's UI changes its hash. Re-sign the tree with
    `baselith plugin sign <path>`, or load it with
    `BASELITH_SKIP_INTEGRITY_CHECK=true` during development (the flag is inert in
    production).

### Hash surface generations

The hashed surface has widened twice. Each generation is a superset of the
previous one and is named by `core.plugins.integrity.HashSurface`:

| Surface      | Releases  | Adds                                                                                   |
| ------------ | --------- | --------------------------------------------------------------------------------------- |
| `V1_SOURCE`  | pre-0.17  | `*.py`/`*.pyi` only                                                                    |
| `V2_BUILD`   | 0.17–0.26 | Build and packaging files, `SKILL.md` bodies                                           |
| `V3_SHIPPED` | 0.27+     | Native modules, shell scripts, served front-end assets (`ui/dist/**`, `static/**`)     |

`CURRENT_HASH_SURFACE` is what the signing tools produce (`V3_SHIPPED`). A
signature that matches only a superseded surface still loads **outside** strict
mode, with a warning naming what its signature does *not* cover; under
`BASELITH_REQUIRE_SIGNED_PLUGINS=true` it is **refused** until the plugin is
re-signed. Re-sign with `baselith plugin sign <path>`; the `sign-changed-plugins`
pre-commit hook does the same automatically, but only when a `*.py`/`*.pyi` file
changed — a UI-only or asset-only change still needs the manual run.

!!! warning "Enforcing signatures"
    In **production** the loader is fail-closed by default: a plugin lacking a valid
    `integrity_sha256` is refused unless `BASELITH_ALLOW_UNSIGNED_IN_PROD=true` is set
    (insecure opt-out). Set `BASELITH_REQUIRE_SIGNED_PLUGINS=true` to enforce signing in
    **every** environment. A mismatch between the computed and declared hash always
    refuses the load.

!!! note "Distribution archives"
    The framework ships no `plugin package` command. To distribute a plugin, publish it to
    the marketplace with `baselith plugin marketplace publish <path>` (which packages and
    uploads it for you), or distribute the plugin directory / a standard archive yourself.

---

## Validation

`baselith plugin validate` takes the plugin **name** and looks for it under `./plugins/`
in the current working directory — not a path:

```bash
# Validates ./plugins/my-plugin
baselith plugin validate my-plugin

# Machine-readable report (exit code 1 when any check fails)
baselith plugin validate my-plugin --format json
```

The entry point may be `plugin.py` or, for a disabled plugin, `plugin.disabled`.

### Validation Checks

The report has one row per check (`core/cli/commands/plugin/local_validate.py`):

1. **Python Syntax**: `plugin.py` parses (AST) without errors
2. **Plugin Class**: a class whose bases include `Plugin`, `AgentPlugin`, `RouterPlugin` or `GraphPlugin` by name
3. **Manifest Parse / Schema**: `manifest.yaml|yml|json` loads and declares `name`, `version`, `description`
4. **Env Variables**: every entry of `environment_variables` is set in the validating shell
5. **Python Deps**: every `python_dependencies` distribution is installed (presence only — the version specifier is not evaluated)
6. **Plugin Deps**: every `plugin_dependencies` name exists as a directory under `plugins/`

Checks 4–6 only run when the corresponding manifest key is non-empty. The validator does
not import the plugin and performs no security or import scan.

### Fixing Common Errors

**"Manifest Parse" failed**

```bash
# From inside the plugin directory: surface the YAML error
python -c "import yaml; yaml.safe_load(open('manifest.yaml'))"
```

**"Manifest Schema — Missing: version"**

```yaml
# Add the missing field
name: my-plugin
version: 1.0.0   # <- Add this
description: Brief description
```

**"Plugin Class — No class extending Plugin, AgentPlugin, RouterPlugin, GraphPlugin"**

The check matches base-class **names** in the source, so `class MyPlugin(Plugin)` and
`class MyPlugin(core.plugins.Plugin)` both pass; an aliased import (`from core.plugins
import Plugin as Base`) does not.

**"Python Deps — Missing: httpx>=0.25,<1.0"**

Install the package into the environment you validate from; the check asks
`importlib.metadata` whether the distribution is present.

**"Plugin Deps — Missing: base-plugin"**

The named plugin must exist as `plugins/base-plugin/` next to yours.

---

## CI/CD Integration

Automate validation, signing and publishing with CI/CD.

### Authenticating a non-interactive pipeline

CI jobs cannot use the interactive login prompt, so supply the credential as a CI
secret. Choose the path that matches who you are.

**Hub operators** set `MARKETPLACE_API_KEY` (the server key) as shown in the
examples below; `publish` picks it up automatically.

**External publishers** store a **GitHub token** (a classic PAT with no scopes)
as a secret and exchange it for a session at the start of the job:

```bash
baselith plugin marketplace login --github-token "$GITHUB_MARKETPLACE_TOKEN"
baselith plugin marketplace publish .
```

Each run mints a fresh ~7-day session JWT — long-lived enough for the job, while
the PAT's own lifetime stays under your control. See
[Marketplace › Authentication](marketplace.md#publishing).

!!! tip "Prefer Backstage for release orchestration"
    The [Backstage Publish template](backstage-publish.md) offers a
    zero-config alternative: the framework's
    `POST /api/backstage/publish` endpoint wraps the zipping + submission
    step for you, and the optional GitHub mirror ships a ready-made
    `marketplace-publish.yml` workflow identical in spirit to the one
    below. Keep the raw GitHub Actions recipe if you need a fully
    air-gapped, Backstage-less release path.

!!! note "Layout the CLI expects"
    `baselith plugin validate` addresses the plugin by **name** under `./plugins/`, while
    `sign` and `marketplace publish` take a **path**. The pipelines below therefore check
    the plugin repository out as `plugins/my-plugin` inside the job workspace and install
    the framework from PyPI to get the `baselith` CLI.

### GitHub Actions

```yaml title=".github/workflows/publish.yml"
name: Publish Plugin

on:
  push:
    tags:
      - 'v*'

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          path: plugins/my-plugin

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install BaselithCore CLI
        run: pip install baselith-core

      - name: Validate plugin
        run: baselith plugin validate my-plugin

      - name: Run tests
        run: pytest plugins/my-plugin/tests

      - name: Sign plugin (write integrity_sha256)
        run: baselith plugin sign plugins/my-plugin

      - name: Publish to marketplace
        env:
          MARKETPLACE_API_KEY: ${{ secrets.MARKETPLACE_API_KEY }}
        run: |
          baselith plugin marketplace publish plugins/my-plugin
```

### GitLab CI

```yaml title=".gitlab-ci.yml"
stages:
  - validate
  - test
  - sign
  - publish

default:
  before_script:
    - pip install baselith-core
    # validate needs the plugin under ./plugins/<name>
    - mkdir -p /tmp/ws/plugins && cp -r "$CI_PROJECT_DIR" /tmp/ws/plugins/my-plugin

validate:
  stage: validate
  script:
    - cd /tmp/ws && baselith plugin validate my-plugin

test:
  stage: test
  script:
    - pytest tests/ --cov=.

sign:
  stage: sign
  script:
    - baselith plugin sign .
  artifacts:
    paths:
      - manifest.yaml

publish:
  stage: publish
  only:
    - tags
  script:
    - baselith plugin marketplace publish .
```

---

## Pre-Publication Checklist

Before publishing, verify:

### Code

- [ ] All tests pass
- [ ] No critical TODO or FIXME
- [ ] Code formatted (black, ruff)
- [ ] Type hints present

### Documentation

- [ ] README.md updated
- [ ] CHANGELOG.md with new changes
- [ ] Docstrings on public classes and functions
- [ ] Usage examples included

### Metadata

- [ ] `manifest.yaml` (or `manifest.json`) passes `baselith plugin validate`
- [ ] Version incremented (SemVer)
- [ ] Dependencies updated
- [ ] `min_core_version` correct (full `MAJOR.MINOR.PATCH`, not above the release you tested on)

### Security

- [ ] No hardcoded secrets
- [ ] Input validation on all endpoints
- [ ] `integrity_sha256` refreshed with `baselith plugin sign`

---

## Troubleshooting

### "Package too large"

**Problem**: The archive built by `baselith plugin marketplace publish` is bigger than
expected.

**Solution**: There is no `exclude` key in the manifest. The publisher zips the plugin
directory itself and already skips dotfiles and dot-directories (`.git/`, `.env`, …),
`__pycache__`, `node_modules`, `.ruff_cache`, `.mypy_cache`, `.pytest_cache`, `.state`,
`build`, `dist` (except `ui/dist`), `*.egg-info`, `ui/src/`, and `*.pyc`/`*.pyo`. Anything
else that should not ship — fixtures, sample data, local docs builds — must be removed
from the tree or moved under one of those directories before publishing.

### "Dependency conflict"

**Problem**: Two Python dependencies require incompatible versions.

**Solution**: Use more flexible version ranges in `python_dependencies` (a list of pip
requirement strings — not a nested `dependencies.python` object):

```yaml
python_dependencies:
  - packageA>=1.0,<3.0   # Wider range
  - packageB>=2.0
```

Do not put Python packages in `dependencies`: that key is a legacy list of **plugin
names**, and each entry is reported as an unmet plugin dependency.

### "Validation failed: missing entry point"

**Problem**: `plugin.py` doesn't contain a valid Plugin class.

**Solution**: Ensure `plugin.py` contains a class extending one of the framework bases:

```python
from core.plugins import Plugin


class MyPlugin(Plugin):
    """Main plugin class."""
```

Identity is not declared on the class — `name`, `version` and the rest come from the
manifest in the same directory, which must exist or the plugin fails to load.

---

## Best Practices

!!! tip "Versioning"
    Use Semantic Versioning (MAJOR.MINOR.PATCH). Never modify an already published version.

!!! tip "Dependencies"
    Specify minimum versions with flexible ranges (`>=1.0`), avoid exact pins (`==1.0.0`) when possible.

!!! warning "Testing"
    Always test the package in a clean environment before publishing. Use virtualenv or Docker.

!!! tip "Changelog"
    Maintain a CHANGELOG.md following the [Keep a Changelog](https://keepachangelog.com/) format.

!!! tip "License — your choice, not the framework's"
    Always include a license and declare it in `manifest.yaml` (`license:`). The
    framework is AGPL-3.0-only, but the
    [BaselithCore Plugin Exception](https://github.com/baselithcore/baselithcore/blob/main/LICENSE.exception)
    grants an additional permission under AGPL section 7: a plugin that uses the
    framework *as a library* may carry **any** license — MIT and Apache-2.0 are
    common for open plugins, and a closed license is equally allowed. The source
    disclosure AGPL section 13 would otherwise impose on network users does not
    reach your plugin.

!!! warning "The Exception asks two things in return"
    Both are conditions, not suggestions — miss either and the plain AGPL governs
    your plugin again.

    **A notice (§3(c)).** Each plugin conveyed under the Exception must state, in
    its documentation or its own license notice, that it is built for BaselithCore,
    that BaselithCore is licensed under AGPL-3.0-only, and where the Corresponding
    Source of the framework version it requires can be obtained. For example:

    ```text
    Built for BaselithCore, which is licensed under AGPL-3.0-only and available at
    https://github.com/baselithcore/baselithcore (see its LICENSE and
    LICENSE.exception). This plugin is licensed under MIT.
    ```

    **No patching the framework (§3(b)).** Registering handlers, routers,
    middleware, services or configuration through the published extension points is
    *use* of the framework. Modifying, patching or replacing files under `core/`
    makes a modified framework — governed by AGPL-3.0-only whatever directory it
    ships in.
