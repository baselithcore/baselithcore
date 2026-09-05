---
title: Marketplace
description: Discover and install BaselithCore plugins
---

## Plugin Marketplace

The **Plugin Marketplace**, accessible at [marketplace.baselithcore.xyz](https://marketplace.baselithcore.xyz), is the central ecosystem for discovering, installing, and managing extensions for BaselithCore. It allows you to enrich your system with new capabilities, integrations, and AI tools with a handful of commands.

!!! info "Where the marketplace lives in the framework"
    Discovery, installation and publishing are built into the framework as the
    `baselith plugin marketplace` command group (`core/cli/commands/plugin/parser.py`),
    backed by the `core.marketplace` package (`PluginRegistry`, `PluginInstaller`,
    `PluginValidator`, `PluginPublisher`). There is no marketplace page in the admin
    console; use the CLI, or the Python API described in
    [Core Modules › Marketplace](../core-modules/marketplace.md).

---

## Using the CLI

The Baselith CLI provides simple commands to manage marketplace plugins from your terminal.

### Commands

| Command | Arguments / flags | What it does |
|---------|-------------------|--------------|
| `list` | `--category <name>` (default `all`), `--refresh` | List every plugin the hub publishes; `--refresh` bypasses the local cache |
| `search <query>` | `--category <name>` | Weighted text search over id, name, description and tags |
| `info <plugin_id>` | — | Show status, author, description, repository, tags, stars and downloads |
| `install <plugin_id>` | `--version <ref>` | Clone the plugin's `https` git repository at branch/tag `<ref>` (default `main`) into `plugins/<name>` |
| `uninstall <plugin_id>` | — | Remove an installed marketplace plugin and its assets |
| `update <plugin_id>` | — | Sync an installed plugin with the latest marketplace version |
| `login` | `--github-token <token>` | Exchange a GitHub token for a session JWT, or paste an existing JWT / API key |
| `logout` | — | Remove the cached credential |
| `identity` | — | Show your marketplace identity and token status |
| `publish <path>` | `--key <key>` (or `MARKETPLACE_API_KEY`) | Package a local plugin directory and upload it to the hub |

### Quick Commands

```bash
# 1. Browse everything, or search for a plugin by keyword
baselith plugin marketplace list
baselith plugin marketplace search "search-tool"

# 2. Get detailed information about a plugin
baselith plugin marketplace info weather-agent

# 3. Install a plugin to your local instance (restart Baselith to load it)
baselith plugin marketplace install weather-agent
baselith plugin marketplace install weather-agent --version v1.2.0

# 4. Update an installed plugin to the latest version
baselith plugin marketplace update weather-agent

# 5. Remove a plugin from your system
baselith plugin marketplace uninstall weather-agent
```

Installed plugins are then handled like any other local plugin: `baselith plugin list`,
`baselith plugin info <name>`, `baselith plugin enable|disable <name>` and the
`/api/plugins` management routes.

---

## Publishing to the Marketplace {#publishing}

Contributing to the BaselithCore ecosystem is simple. Once your plugin is ready and follows the [Packaging Guidelines](packaging.md), you can share it with the world.

!!! tip "Prefer the Backstage Scaffolder"
    The modern, recommended path is the **one-click Backstage flow** — the
    Scaffolder fetches the plugin from the monorepo, renders the required
    overlay (LICENSE, manifest patch, requirements), and POSTs the bundle
    to the marketplace hub through the framework's
    `POST /api/backstage/publish` endpoint. No local `git init` or ZIP
    gymnastics required. See [Backstage Publish](backstage-publish.md)
    for the full walkthrough. The CLI commands below remain supported as
    an escape hatch.

### Scaffolding a new plugin

The fastest way to start is the `baselith` CLI, which generates a skeleton that already respects the [packaging guidelines](packaging.md) (lowercase-hyphenated id, SemVer version, manifest with the required fields):

```bash
# Scaffold an agent plugin
baselith plugin create weather-agent --type agent

# Or run the interactive wizard (prompts for author, category, etc.)
baselith plugin create --interactive
```

The `--type` flag accepts `agent`, `router`, or `graph`. The generated directory contains `manifest.json`, `__init__.py` and `plugin.py`, plus `agent.py` for the `agent` type or `router.py` for the `router` type. No `README.md` is generated — add one before publishing. Edit `plugin.py`, declare metadata and dependencies in the manifest, then proceed with authentication and publish.

### 1. Authentication

Publishing requires a **marketplace session** — a short-lived JWT bound to your
GitHub identity that tells the hub *who* is publishing. (This is distinct from
the [integrity signature](#plugin-integrity) written by `baselith plugin sign`,
which proves *what* the plugin contains.)

The quickest way to authenticate is to exchange a GitHub token for a session:

```bash
# Create a GitHub token at https://github.com/settings/tokens
# A classic PAT with NO scopes is enough — the hub only reads your public profile.
baselith plugin marketplace login --github-token <github-token>
```

The GitHub token is used once for the exchange and is never stored; only the
resulting session JWT is saved to `~/.baselith/credentials.json` (valid ~7 days).

Alternatively, run `baselith plugin marketplace login` with no flag and paste an
existing marketplace JWT (operators may instead save a server API key this way).
Check your current identity and token status at any time:

```bash
baselith plugin marketplace identity
```

### 2. Publish Your Plugin

Navigate to your plugin directory and run the publish command. This will validate your manifest, package your assets, and upload them to the central hub.

```bash
baselith plugin marketplace publish .
```

!!! tip "Local Validation"
    Always run `baselith plugin validate <plugin-name>` (with the plugin under `./plugins/`) before publishing to ensure your configuration is correct and all dependencies are properly defined.

---

## Trust & Security

Every plugin in the marketplace undergoes an automated **Security Scan** and **Validation** process before being listed. This ensures that:

- **Safety**: Plugins are checked for malicious code and common vulnerabilities.
- **Compatibility**: Each version is verified to work with your current BaselithCore version.
- **Resource Protection**: Automated checks prevent plugins from consuming excessive system resources.

!!! tip "Verified Plugins"
    Look for the **Verified** badge to find plugins that have undergone additional manual review for quality and security.

---

## Security & Centralization

To maintain the integrity of the BaselithCore ecosystem, the marketplace follows a **Centralized Trust Model**:

- **Unified Registry**: Discovery, installation, and updates are coordinated through the official marketplace hub. This ensures consistency and security across all BaselithCore instances.
- **Secure Publishing**: The `baselith plugin marketplace publish` command is strictly locked to the **official marketplace**. This prevents developers from accidentally (or maliciously) uploading sensitive code to unauthorized registries.
- **Transport Restrictions**: Marketplace installations only accept plugin repositories exposed through `https` clone URLs. Entries with embedded credentials or non-HTTPS transports are rejected before installation.

Every submission is automatically validated for security vulnerabilities before being accepted into the global registry. Archive size, structure, and contents are inspected before the package is ever unpacked.

### Declaring requirements

Plugins may declare constraints on both BaselithCore and their Python runtime in `manifest.yaml`:

```yaml
min_core_version: "0.29.0"
python_dependencies:
  - httpx>=0.25,<1.0
plugin_dependencies:
  base-plugin: ">=1.0.0"
```

`python_dependencies` entries are pip requirement strings (PEP 440), so bounded ranges
(`>=1.0,<2.0`) and compatible-release specifiers (`~=1.24`) work. `min_core_version`,
`max_core_version` and each `plugin_dependencies` value use the framework's own
constraint parser (`core/plugins/version.py`): a full `MAJOR.MINOR.PATCH` version with a
single operator (`==`, `!=`, `>`, `>=`, `<`, `<=`, `^`, `~`). `plugin_dependencies` is a
**mapping** of plugin name → constraint, not a list.

These constraints are evaluated when the plugin loads, not at install time. By default an
unsatisfied constraint is logged as a warning and the plugin still loads; set
`BASELITH_ENFORCE_PLUGIN_COMPAT=true` to skip incompatible plugins instead
(`core/plugins/load_gates.py`).

---

## Plugin Integrity

Plugins may declare an `integrity_sha256` digest in their manifest. The loader verifies it via `core.plugins.integrity.verify_plugin_integrity` before executing any plugin code. Compute and embed the digest with `baselith plugin sign <path>` — see the [Packaging Guide](packaging.md#integrity) for details. Operators can require a valid digest on every plugin by setting `BASELITH_REQUIRE_SIGNED_PLUGINS=true`.
