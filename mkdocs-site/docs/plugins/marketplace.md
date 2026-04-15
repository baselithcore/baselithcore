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

Every submission is automatically validated for security vulnerabilities before being accepted into the global registry.

---

## 🔑 Publisher Signatures (Ed25519) {#signatures}

Publishers may sign plugin archives with an **Ed25519** key registered to their marketplace account. When signature enforcement is enabled, the hub rejects any submission that does not carry a valid signature from an active publisher key.

### Why sign

- Proves the ZIP was produced by the account that owns the key, not by a compromised intermediary.
- Aligns with OWASP A08 (Software Integrity) — signed packages and checksum verification.
- Gives the hub an auditable `audit.signature_verified` event per accepted submission.

### Key lifecycle

1. **Generate a keypair** on the publisher machine:

    ```bash
    python scripts/keygen.py mykey-2026
    # writes mykey-2026.key (chmod 600) and mykey-2026.pub (PEM)
    ```

2. **Register the public key** under your account (authenticated request):

    ```bash
    curl -X POST "$HUB_URL/api/marketplace/plugins/keys" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"key_id\": \"mykey-2026\", \"public_key_pem\": \"$(cat mykey-2026.pub)\"}"
    ```

3. **Configure the publisher** to sign every submission:

    ```bash
    export MARKETPLACE_PUBLISHER_PRIVATE_KEY_PATH=/secure/path/mykey-2026.key
    export MARKETPLACE_PUBLISHER_KEY_ID=mykey-2026
    baselith plugin marketplace publish .
    ```

4. **List** or **revoke** keys when rotating:

    ```bash
    curl "$HUB_URL/api/marketplace/plugins/keys" -H "Authorization: Bearer $TOKEN"
    curl -X DELETE "$HUB_URL/api/marketplace/plugins/keys/mykey-2026" \
      -H "Authorization: Bearer $TOKEN"
    ```

### What is signed

The publisher client signs the canonical byte string:

```text
sha256(zip_bytes) ":" plugin_id ":" version
```

The signature is sent as the multipart form field `signature` (base64) alongside the ZIP, with an optional `key_id` field disambiguating which active key was used. The hub retrieves every active public key for the authenticated publisher and accepts the submission if any of them verifies the signature.

### Enforcement

Set `MARKETPLACE_REQUIRE_SIGNATURES=true` on the hub to reject unsigned submissions. Until enforcement is enabled the hub accepts unsigned archives for backward compatibility, but every verified signature is still recorded.

!!! warning "Key storage"
    Private keys must be kept on the publisher side only. Treat them like any production credential — restricted file permissions (`chmod 600`), secret managers in CI, and immediate revocation via `DELETE /api/marketplace/plugins/keys/{key_id}` if a leak is suspected.
