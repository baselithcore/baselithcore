---
title: Marketplace
description: Discover and install BaselithCore plugins
---

## Plugin Marketplace

The **Plugin Marketplace**, accessible at [marketplace.baselithcore.xyz](https://marketplace.baselithcore.xyz), is the central ecosystem for discovering, installing, and managing extensions for BaselithCore. It allows you to enrich your system with new capabilities, integrations, and AI tools with just a few clicks.

!!! info "Simplified Experience"
    Marketplace discovery and installation are built directly into the BaselithCore framework. You can manage your plugins through the **Dashboard Console** or the **CLI**.

---

## 🖥️ Using the Dashboard

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

## 💻 Using the CLI

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

## 🚀 Publishing to the Marketplace {#publishing}

Contributing to the BaselithCore ecosystem is simple. Once your plugin is ready and follows the [Packaging Guidelines](packaging.md), you can share it with the world.

### Scaffolding a new plugin

The fastest way to start is the `baselith` CLI, which generates a ready-to-publish skeleton that already respects the [packaging guidelines](packaging.md) (lowercase-hyphenated id, SemVer version, required files):

```bash
baselith plugin init weather-agent \
  --author "Your Name" \
  --category agent \
  --description "Fetches forecasts from any provider"
```

The generated directory contains `manifest.yaml`, `plugin.py`, `README.md`, and `requirements.txt`. Edit `plugin.py`, declare dependencies in `manifest.yaml`, then proceed with authentication and publish.

### 1. Authentication

Before publishing, you must authenticate your CLI with your Marketplace API key.

```bash
baselith plugin marketplace login
```

### 2. Publish Your Plugin

Navigate to your plugin directory and run the publish command. This will validate your manifest, package your assets, and upload them to the central hub.

```bash
baselith plugin marketplace publish .
```

!!! tip "Local Validation"
    Always run `baselith plugin validate` before publishing to ensure your configuration is correct and all dependencies are properly defined.

---

## 🛡️ Trust & Security

Every plugin in the marketplace undergoes an automated **Security Scan** and **Validation** process before being listed. This ensures that:

- **Safety**: Plugins are checked for malicious code and common vulnerabilities.
- **Compatibility**: Each version is verified to work with your current BaselithCore version.
- **Resource Protection**: Automated checks prevent plugins from consuming excessive system resources.

!!! tip "Verified Plugins"
    Look for the **Verified** badge to find plugins that have undergone additional manual review for quality and security.

---

## 🔒 Security & Centralization

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

## 🔑 Publisher Signatures {#signatures}

Publishers can sign their plugin archives so the marketplace can prove that a ZIP was produced by the account that owns the signing key. Signing is optional today and may become required for publishing in the future; we recommend enabling it now.

### Publisher workflow

1. **Generate a keypair** on your machine. Keep the private key local — only the public key ever leaves your workstation:

    ```bash
    baselith plugin marketplace keygen my-key-2026
    ```

2. **Register the public key** under your account from the **Publisher Keys** tab in the marketplace UI (paste the public PEM) — or from the CLI:

    ```bash
    baselith plugin marketplace keys add my-key-2026 my-key-2026.pub
    ```

3. **Tell the publisher CLI** which key to use, then publish as usual:

    ```bash
    export MARKETPLACE_PUBLISHER_PRIVATE_KEY_PATH=/secure/path/my-key-2026.key
    export MARKETPLACE_PUBLISHER_KEY_ID=my-key-2026
    baselith plugin marketplace publish .
    ```

4. **Rotate or revoke** keys at any time from the **Publisher Keys** tab or via:

    ```bash
    baselith plugin marketplace keys revoke my-key-2026
    ```

!!! warning "Key storage"
    Private keys must remain on the publisher side only. Treat them like any production credential — restricted file permissions (`chmod 600`), secret managers in CI, and immediate revocation if a leak is suspected.
