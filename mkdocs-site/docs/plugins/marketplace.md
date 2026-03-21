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

Every submission is automatically validated for security vulnerabilities before being accepted into the global registry.
