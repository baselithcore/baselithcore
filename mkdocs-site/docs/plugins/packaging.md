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
permissions:                      # Optional. What this plugin is allowed to do.
  network:
    egress: ["api.github.com", "*.openai.com"]
  tools: ["search_knowledge_base"]
  secrets: ["MY_PLUGIN_API_KEY"]
  filesystem: ["./data/plugins/my-plugin"]
entry_point: plugin:MyPlugin      # Optional. Which class to instantiate.
integrity_sha256: 7c2a1b...e9f0   # Optional. SHA-256 of everything the plugin ships and runs, this manifest included.
hash_surface_version: 5           # Written by `baselith plugin sign`. Advisory.
```

### Manifest Fields

| Field                   | Required | Description                                      |
| ----------------------- | -------- | ------------------------------------------------ |
| `name`                  | ✅        | Unique plugin name (lowercase, hyphen-separated) |
| `display_name`          | ❌        | Human-readable name for consoles and the Backstage title (e.g. "CV Intake"); presentation only — `name` stays the identifier |
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
| `environment_variables` | ❌        | Required environment variables: names, or mappings with `name`, `description`, `required` |
| `entry_point`           | ❌        | Which class to instantiate, as `module:Class` (also `:Class` or a bare `Class`). The module half resolves inside the plugin's own package. Without it the loader falls back to finding the single concrete `Plugin` subclass in `plugin.py` — see [Entry points](#entry-point). |
| `permissions`           | ❌        | What the plugin is allowed to do — see [Permissions](#permissions). `integrity_sha256` proves *which code* runs; this declares what that code may do. |
| `integrity_sha256`      | ❌        | Hex SHA-256 over everything the plugin ships and runs, **this manifest included** — see [What is hashed](#integrity) for the exact surface. Verified before `exec_module`; mismatch refuses load. In production a plugin without this field is refused by default (fail-closed) unless `BASELITH_ALLOW_UNSIGNED_IN_PROD=true`; set `BASELITH_REQUIRE_SIGNED_PLUGINS=true` to reject unsigned plugins in every environment. Compute via `baselith plugin sign` or `core.plugins.integrity.compute_plugin_hash()`. |
| `signature_ed25519`     | ❌        | Hex Ed25519 signature over `integrity_sha256`, written by the signing tools. Checked against the trust roots / trust store when `BASELITH_REQUIRE_PLUGIN_SIGNATURES=true` — see [Trusted publishers](../advanced/security.md#plugin-trust-store). |
| `hash_surface_version`  | ❌        | Which generation of the hashed surface `integrity_sha256` was computed under — currently `5`. **Advisory only**: it is written after the digest and excluded from it, so verification never reads it. Use it for display and "needs re-signing" nudges. |
| `x-…`                   | ❌        | **Vendor extensions.** Any top-level key prefixed `x-` is legal, carried verbatim and never interpreted by the core — see [Vendor extensions](#vendor-extensions). |

The class in `plugin.py` carries no identity of its own: `name`, `version` and every other
field are read from the manifest next to it (`core/plugins/_metadata.py`).

!!! warning "Unknown keys are refused"
    The manifest schema (`core.plugins.manifest_model.PluginManifestModel`) is
    `extra="forbid"`: a key outside the table above fails validation and the plugin is
    refused in every environment, with a did-you-mean hint —
    `unknown manifest key(s): 'min_core_verison' (did you mean 'min_core_version'?)`.
    A plugin with **no** manifest at all still loads; a plugin with a *broken* one never
    does, because a manifest that will not parse declares no `permissions` and would
    otherwise be loaded with more authority than its author asked for.

    If the key is **not** a typo — data your own plugin reads and the core does not
    interpret — the legal home for it is the `x-` prefix, and the error says so:
    `'control' (vendor data? declare it as 'x-control')`. See
    [Vendor extensions](#vendor-extensions).

### Vendor extensions (`x-`) {#vendor-extensions}

A fail-closed schema has exactly as many legal keys as the *core* understands,
which left nowhere to put data only your plugin reads. Any top-level key starting
with **`x-`** is now legal, kept verbatim, and never interpreted by the core:

```yaml title="manifest.yaml"
name: my-plugin
version: 1.0.0
description: Brief but informative plugin description

x-control:                     # yours; the core carries it and ignores it
  mode: strict
  refresh_seconds: 30
x-acme.io/feature-2: enabled   # reverse-DNS style also fits the key grammar
```

Read it back from your own plugin code, with or without the prefix:

```python
class MyPlugin(Plugin):
    async def initialize(self, config: dict[str, Any]) -> None:
        await super().initialize(config)
        control = self.metadata.extension("control")             # -> {"mode": …} | None
        limits = self.metadata.extension("x-acme.io/feature-2", default=[])
```

`PluginMetadata.extensions` is the whole mapping, keyed as written; `to_dict()`
re-emits the keys at top level, so a metadata round-trip is still a valid manifest.

**The two properties that make this a namespace and not a loophole:**

- **A typo in a known key is still refused.** The prefix is checked *before*
  validation, and no core key starts with `x-`, so the two namespaces are
  syntactically disjoint. `min_core_verison` cannot accidentally acquire a prefix
  — it still fails with `did you mean 'min_core_version'?`, and that hint wins
  whenever a close known key exists.
- **An extension is still signed.** V5 hashes the canonicalised manifest minus the
  three self-referential keys, so `x-` keys are inside the digest by construction.
  Adding, editing or removing one changes `integrity_sha256` — widening
  `x-control` on a signed plugin breaks its signature exactly like widening
  `permissions` does. Re-sign after editing an extension.

| Rule | Detail |
| --- | --- |
| Grammar | `^x-[A-Za-z0-9][A-Za-z0-9._/-]*$` — a letter or digit after the prefix, then letters, digits, `.`, `_`, `-` or `/`. A bare `x-` is a **malformed extension key**, not an unknown one, and says so. |
| Case and separator | Lowercase `x-` only. `X-Control` and `x_control` are **refused**, with the legal spelling suggested (`declare it as 'x-control'`). |
| Shadowing | `x-permissions` is fine. The namespaces are disjoint by definition, so rejecting shadows would make every future core key a breaking change for plugins already using the prefixed name. |
| Collisions between plugins | Not policed — two plugins may both use `x-control` with different meanings. Extensions are per-plugin and never merged, so this is harmless; a reverse-DNS name (`x-acme.control`) avoids ambiguity and the grammar already allows it. |

!!! note "`x-` keys are not part of the documented key surface"
    `known_manifest_keys()` — what the CLI validator and the marketplace read —
    stays exactly the set of keys the core interprets. Extensions are deliberately
    outside it, and there is no `extensions:` block: that would be a second,
    unprefixed door.

### Entry points {#entry-point}

Two things called "entry point" meet here; they are unrelated.

- **`entry_point:` in the manifest** names the class inside the plugin's own package.
  Declare it whenever `plugin.py` exposes more than one concrete `Plugin` subclass —
  ambiguity is a hard error (`PluginClassError`), not an alphabetical coin flip, and the
  message tells the author to add this key.
- **`entrypoint:`** is accepted only as a legacy spelling for existing plugin
  manifests. New plugins should declare `entry_point:`.
- **The `baselith.plugins` distribution entry-point group** lets an *installed* package
  publish a plugin without dropping a directory into `plugins/` — see
  [Shipping a plugin as a distribution](#distribution-entry-points).

### Docker Installation Contract

`baselith plugin add <repository> --docker` uses the existing dependency and
version fields plus optional frontend and health metadata:

```yaml
entry_point: plugin:MyPlugin
frontend:
  path: ui
  package_manager: pnpm
  build_command: pnpm build
  output_dir: out
health_endpoint: /my-plugin/
```

`path` is relative to the plugin directory (default `ui`); `output_dir` is
relative to `path` (default `dist`). `baselith doctor` resolves the pair the
same way in its *Plugin Frontends* check and warns when the declared output is
not on disk, so a plugin that ships a UI should always declare the block — the
warning is what catches an unbuilt SPA before it serves a 404.

The package manager must be `npm`, `pnpm` or `yarn`. Commit the corresponding
lockfile: the builder uses `npm ci` or frozen-lockfile installation. It runs in
Node 22 on Docker and checks for a nonempty output directory. `frontend: false`
disables automatic detection. Legacy main plugins can still use UI detection;
dependencies needing a frontend rebuild must declare this block explicitly.

The declared health path must return 200 without authentication or redirects.
Otherwise use a dedicated readiness route. An agent-only plugin should not rely
on the default `/<name>/` probe unless it actually serves that route.

Installation still expects `plugin.py`; `entry_point` does not enable arbitrary
Python package layouts. Python dependencies are installed into the API image,
with a final `pip check`. Version conflicts fail the build. Missing Core bounds
remain a legacy warning; invalid or incompatible declared bounds fail installation.
See [Docker Core and Plugins](../getting-started/docker-core.md) for the runbook.

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

!!! warning "Fail-closed by default"
    Core-version bounds and `plugin_dependencies` are checked when the plugin loads
    (`core/plugins/load_gates.py`), and an unsatisfied declaration **skips the plugin**.
    `BASELITH_ENFORCE_PLUGIN_COMPAT=false` (or `0`/`no`/`off`) is the explicit downgrade
    back to warn-only, meant for booting a deployment while a manifest is corrected —
    setting it to `true` changes nothing, because that is already the default.
    `BASELITH_ENFORCE_PLUGIN_CONFIG` reads the same way for the config-schema gate.

    `plugin_dependencies` are part of the gate, so **disabling a dependency disables its
    dependents**: turning `browser_agent` off in `configs/plugins.yaml` now skips
    `baselithbot`, where previously it logged a warning and loaded anyway.

---

## Permissions {#permissions}

A plugin runs **in-process with the host's full authority**: any egress host,
any file, any secret in the environment, any tool. `integrity_sha256` proves
*which code* is running; it does not say whether that code should be able to
reach `169.254.169.254`. The `permissions:` block answers the second question.

```yaml
permissions:
  network:
    egress: ["api.github.com", "*.openai.com"]
  tools: ["search_knowledge_base", "scrape_url"]
  secrets: ["MY_PLUGIN_API_KEY"]
  filesystem: ["./data/plugins/my-plugin"]
```

`*` grants everything; `*.example.com` matches any subdomain but neither the
apex nor `api.example.com.evil.net`. **Declare `"*"` honestly** where egress is
driven by the user — a scraper reaches whatever URL it is given, and saying so
tells an operator more than a list that is quietly incomplete.

An **empty** block is a statement ("this plugin needs nothing"). A **missing**
block only means the manifest predates the mechanism. The two are treated
differently, which is what makes the rollout safe.

### Rollout: declare, observe, enforce

`BASELITH_PLUGIN_PERMISSIONS` selects the stage:

| Value | Behaviour |
| --- | --- |
| `off` | Declarations are parsed and exposed, never consulted. |
| `warn` *(default)* | A call outside the declared set is logged once per plugin and host, and proceeds. The observation window. |
| `enforce` | A plugin that **declared** a block is held to it. A plugin that declared **nothing** is untouched. |

That last row is the property that makes the flag safe to turn on: undeclared
means "not migrated yet", not "denied everything", so enabling enforcement
cannot brick plugins written before the block existed. Same shape as
`BASELITH_REQUIRE_SIGNED_PLUGINS`.

### What is enforced today

| Block | Enforced | Chokepoint |
| --- | --- | --- |
| `network.egress` | ✅ | `core/security/ssrf.py` — every screened outbound URL |
| `tools` | ✅ | `core.orchestration.enforcement.enforce_tool_invocation` |
| `secrets` | ✅ | `core.security.secrets.get_secret` |
| `filesystem` | ❌ | parsed and surfaced only — see below |

**Egress.** Every outbound plugin request already passes the SSRF guard, which
is where the per-plugin decision is made — the guard screens the *address*, the
permission screens *which plugin* may reach it. A refused call raises
`core.plugins.egress.EgressNotPermittedError`, a subclass of `SsrfError`, so
callers that already handle a blocked outbound target handle this too.

**Tools.** `enforce_tool_invocation` is the gate every orchestrated tool call
passes; the permission check sits beside the contract check, because both
answer "may this tool run at all?" before the approval and budget questions of
whether it should. A refusal raises `core.plugins.access.ToolNotPermittedError`.

**Secrets.** `get_secret` consults the declaration before resolving the value,
so a refused read never materialises the credential. A refusal raises
`core.plugins.access.SecretNotPermittedError`.

**`filesystem` is still not enforced.** A plugin's file access has no
comparable chokepoint — it is `open()` — so a guard would imply a guarantee
that does not exist. The block is parsed, exposed through
`PluginMetadata.to_dict()` and surfaced by the marketplace; treat it as
documentation until this table says otherwise.

### What this is not

A plugin runs in-process. It can call `os.environ` directly, or let an SDK do
its own credential lookup, and no in-process guard can stop it. What the
declaration buys is that **the framework's own paths** are held to it, and that
an operator reading a manifest knows what a plugin says it needs. Real
containment requires process isolation, which is a different design.

Guards are installed at startup by `core.plugins.guards.install_plugin_guards`,
which points them at the loaded registry. The mode is resolved per call, so a
deployment can move from `warn` to `enforce` without a restart.

### The official plugins

Every plugin under `plugins/` declares a block. Three declare `egress: ["*"]`
(`baselithbot`, `browser_agent`, `web_scraper`) plus `document_sources`, because
their targets are supplied per request — that is the honest declaration, and it
still tells an operator these are the outbound ones. The rest declare an empty
network set, which under `enforce` means they are refused any screened outbound
call at all.

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

In practice the last row means `ui/{dist,out,build}/**` and `static/**`. `.svg`
counts as executable because a same-origin SVG opened top-level runs its embedded
script, and `.css` because it rewrites what the operator sees and clicks.

The **manifest** is covered too, and is the one input that does not contribute its
raw bytes — see [The manifest is signed](#manifest-in-digest) below.

Excluded from the digest:

- `__pycache__/`, `.git/`, `node_modules/`, and `target/` (Cargo's build directory —
  it is gitignored, never distributed, and full of `.dylib`/`.so`/`.sh` files that
  would otherwise make a plugin's hash depend on whether `cargo build` had been run).
- Everything under `ui/` **except** the compiled bundles `ui/dist/**`, `ui/out/**`
  and `ui/build/**` (Vite, a Next.js static export, Create React App). `ui/src/`,
  `ui/node_modules/` and the tsconfig/vite build inputs never ship (mirroring
  `[tool.setuptools.exclude-package-data]` in the plugin's `pyproject.toml`), so
  they stay out.
- `*.json`, `*.ts`/`*.tsx`, images, and Markdown other than `SKILL.md`.

#### The manifest is signed {#manifest-in-digest}

Up to V4 the manifest was deliberately left out of the digest, so that a publisher
could inject `integrity_sha256` after computing it. The price was that the file
deciding the plugin's `name`, its declared `permissions` (egress, tools, secrets),
its `python_dependencies` and its `min_core_version` was the one file a signed
plugin could rewrite freely: anyone able to edit `manifest.yaml` could widen a
signed plugin's egress without breaking either the hash or the Ed25519 signature
over it.

From V5 the digest covers a **canonical projection** of the manifest rather than
its bytes (`core.plugins.integrity.canonical_manifest_bytes`): the file is parsed,
`integrity_sha256`, `signature_ed25519` and `hash_surface_version` are dropped, and
what remains is dumped as compact JSON with sorted keys and appended last under a
reserved label. So:

- injecting the three supply-chain keys after computing the digest still leaves the
  digest unchanged — the publishing workflow is untouched;
- comments, key order, quoting style and YAML-vs-JSON spelling are free; a JSON
  manifest hashes identically to its YAML twin;
- **every other key moves the hash.** Editing `permissions:` on a signed plugin now
  breaks verification, which is the entire point of the change.

A manifest that will not parse contributes its raw bytes instead of being skipped,
so a broken manifest is still covered. A directory with no manifest at all hashes
exactly as it did under V4.

!!! warning "Re-sign after `npm run build`, and after editing the manifest"
    Since 0.27 the compiled dashboard (`ui/dist/**`) is part of the hashed
    surface, so rebuilding a plugin's UI changes its hash — and since V5 so does
    editing any manifest key other than the three supply-chain ones. Re-sign the
    tree with `baselith plugin sign <path>`, or load it with
    `BASELITH_SKIP_INTEGRITY_CHECK=true` during development (the flag is inert in
    production).

### Hash surface generations

The hashed surface has widened several times. Each generation is a superset of the
previous one and is named by `core.plugins.integrity.HashSurface`:

| Surface         | Releases  | Adds                                                                                   |
| --------------- | --------- | --------------------------------------------------------------------------------------- |
| `V1_SOURCE`     | pre-0.17  | `*.py`/`*.pyi` only                                                                    |
| `V2_BUILD`      | 0.17–0.26 | Build and packaging files, `SKILL.md` bodies                                           |
| `V3_SHIPPED`    | 0.27+     | Native modules, shell scripts, served front-end assets (`ui/dist/**`, `static/**`)     |
| `V4_UI_EXPORT`  | 0.31+     | Front-end bundles written outside `ui/dist` — `ui/out/**` (Next.js export), `ui/build/**` (CRA) |
| `V5_MANIFEST`   | 0.33+     | The canonicalised manifest — `permissions`, `python_dependencies`, `min_core_version`, `name`, `entry_point` |

`CURRENT_HASH_SURFACE` is what the signing tools produce (`V5_MANIFEST`), and every
signed tree under `plugins/` now declares `hash_surface_version: 5` — the **nine**
official plugins covered by `scripts/check_official_plugin_typing.py`, plus the
`example-plugin` authoring reference. (`example-plugin` is signed but outside the
typing gate: its hyphenated name is not a Python identifier, so mypy cannot name
the package.) A signature that matches
only a superseded surface still loads **outside** strict mode, with a warning naming
what its signature does *not* cover; under `BASELITH_REQUIRE_SIGNED_PLUGINS=true` it
is **refused** until the plugin is re-signed. Re-sign with
`baselith plugin sign <path>`, or `python scripts/sign_changed_plugins.py <path>`
(also `--all`); the `sign-changed-plugins` pre-commit hook does the same
automatically for any staged change to a hashed path *or* to a manifest.

!!! danger "A V4 signature does not cover the manifest"
    `BASELITH_REQUIRE_PLUGIN_SIGNATURES=true` **without**
    `BASELITH_REQUIRE_SIGNED_PLUGINS=true` still accepts a V4-era signature, and a V4
    signature says nothing about `permissions:` — so the egress-widening this change
    closes is still open on any plugin that has not been re-signed. Re-sign your
    plugins at V5, or turn on `BASELITH_REQUIRE_SIGNED_PLUGINS` as well, which refuses
    every superseded surface.

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

## Shipping a plugin as a distribution {#distribution-entry-points}

A plugin installed as a wheel has no directory under `plugins/`, so the filesystem
scan never saw it. A distribution can now advertise itself through the standard
`baselith.plugins` entry-point group (`core/plugins/discovery.py`):

```toml title="pyproject.toml"
[project.entry-points."baselith.plugins"]
my-plugin = "my_package.my_plugin"
```

The value names an **importable package whose directory contains a manifest** — a
`:Class` suffix or an `[extra]` marker is tolerated and ignored, because the target
is the directory, not the class. Which class to instantiate is still the manifest's
`entry_point`.

| Rule | Behaviour |
| --- | --- |
| Precedence | The `plugins/` directory scan wins. A name clash keeps the local tree, ignores the installed package and logs a warning naming both paths. |
| Failure containment | Broken distribution metadata, an entry point that no longer imports, or a package with no manifest is logged and skipped. Discovery can never be the reason the process fails to start. |
| Kill switch | `BASELITH_DISABLE_PLUGIN_ENTRY_POINTS=true` considers only the `plugins/` directory (default `false`). |
| Config | The `configs/plugins.yaml` filter and `enabled: false` apply to entry-point plugins exactly as they do to directory ones. |

!!! warning "Resolution imports the parent package"
    `importlib.util.find_spec("pkg.sub")` imports `pkg`, before any integrity check
    runs. The distribution is already installed in the interpreter's environment and
    opted in by declaring the entry point, and the plugin's own code is still
    integrity- and signature-verified in `PluginLoader.load_plugin` before
    `exec_module`. A deployment that wants zero tolerance for that should set the kill
    switch.

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
- [ ] `integrity_sha256` refreshed with `baselith plugin sign` — **after** the last
      manifest edit and the last `npm run build`, both of which move the digest
- [ ] `hash_surface_version: 5` present (the signing tools stamp it for you)
- [ ] `permissions:` reflects what the plugin actually does, now that the block is
      inside the signature

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
manifest in the same directory. A plugin with no manifest at all is the legacy shape
and still loads with framework defaults; a plugin whose manifest is present but
**invalid** is refused outright, in every environment.

### "module exposes more than one Plugin subclass"

**Problem**: `plugin.py` defines (or imports) two concrete `Plugin` subclasses, so the
loader will not guess which one is the plugin.

**Solution**: Name it in the manifest:

```yaml title="manifest.yaml"
entry_point: plugin:WidgetPlugin
```

A class imported from a library the plugin depends on does not create ambiguity by
itself: when several candidates exist, the ones defined inside the plugin's own
package win, and a lone candidate is accepted wherever it was defined.

### "unknown manifest key(s)"

**Problem**: The manifest carries a key the schema does not define — usually a typo.

**Solution**: The message names every offending key at once and suggests the closest
valid one. Fix the spelling or delete the key; there is no "ignore unknown keys" mode,
because a silently-dropped `permissions` or `min_core_version` is exactly the failure
the strict schema exists to prevent.

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
