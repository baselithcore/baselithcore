---
title: Marketplace
description: Discover and install BaselithCore plugins
---

## Plugin Marketplace

The **Plugin Marketplace**, accessible at [marketplace.baselithcore.xyz](https://marketplace.baselithcore.xyz), is the central ecosystem for discovering, installing, and managing extensions for BaselithCore. It allows you to enrich your system with new capabilities, integrations, and AI tools with just a few clicks.

!!! info "Simplified Experience"
    Marketplace discovery and installation are built directly into the BaselithCore framework. You can manage your plugins through the **Dashboard Console** or the **CLI**.

---

## Using the Dashboard

The easiest way to explore the marketplace is through the **BaselithCore Console**.

1. **Navigate**: Open your dashboard and look for the **Marketplace** tab in the sidebar.
2. **Discover**: Browse the list of available plugins, including featured tools and community extensions.
3. **Details**: Click on any plugin to read its description, see the current version, and check user ratings.
4. **Install**: Click the "Install" button to add the plugin to your system instantly.
5. **Manage**: View your installed plugins, check for updates, or remove them as needed.

### User Reviews & Ratings

You can interact with the community by:

- **Checking Ratings**: See how other users have rated a plugin before installing.
- **Reading Reviews**: Get insights into the plugin's performance and reliability.
- **Sharing Feedback**: Submit your own ratings and comments for plugins you use.

---

## Using the CLI

For power users and automation, the Baselith CLI provides simple commands to manage plugins from your terminal.

### Quick Commands

```bash
# 1. Search for a plugin by keyword
baselith plugin marketplace search "search-tool"

# 2. Get detailed information about a plugin
baselith plugin marketplace info weather-agent

# 3. Install a plugin to your local instance
baselith plugin marketplace install weather-agent

# 4. Update an installed plugin to the latest version
baselith plugin marketplace update weather-agent

# 5. Remove a plugin from your system
baselith plugin marketplace uninstall weather-agent
```

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

The fastest way to start is the `baselith` CLI, which generates a ready-to-publish skeleton that already respects the [packaging guidelines](packaging.md) (lowercase-hyphenated id, SemVer version, required files):

```bash
# Scaffold an agent plugin
baselith plugin create weather-agent --type agent

# Or run the interactive wizard (prompts for author, category, etc.)
baselith plugin create --interactive
```

The `--type` flag accepts `agent`, `router`, or `graph`. The generated directory contains the manifest, `plugin.py`, and `README.md`. Edit `plugin.py`, declare metadata and dependencies in the manifest, then proceed with authentication and publish.

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
    Always run `baselith plugin validate` before publishing to ensure your configuration is correct and all dependencies are properly defined.

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
min_core_version: "2.0.0"
python_dependencies:
  - httpx>=0.25,<1.0
plugin_dependencies:
  - base-plugin>=1.0
```

The marketplace refuses to install a plugin whose constraints cannot be satisfied by the host. Use standard SemVer / PEP 440 range syntax — single pins, bounded ranges (`>=1.0,<2.0`), and compatible-release specifiers (`~=1.24`) are all supported.

---

## Plugin Integrity

Plugins may declare an `integrity_sha256` digest in their manifest. The loader verifies it via `core.plugins.integrity.verify_plugin_integrity` before executing any plugin code. Compute and embed the digest with `baselith plugin sign <path>` — see the [Packaging Guide](packaging.md#integrity) for details. Operators can require a valid digest on every plugin by setting `BASELITH_REQUIRE_SIGNED_PLUGINS=true`.
