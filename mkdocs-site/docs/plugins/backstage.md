---
title: Backstage Integration
description: Automated software cataloging and plugin scaffolding with Backstage
---

<!-- markdownlint-disable-file MD029 MD030 MD025 -->

# Backstage Integration

BaselithCore provides a native integration with [Backstage](https://backstage.io), an open platform for building developer portals. This integration allows your Baselith instance to automatically export its architecture and plugins to a centralized software catalog, and enables standard plugin scaffolding directly from the Backstage UI.

---

## 1. Overview

The integration consists of three primary components:

1. **Backstage Entity Provider**: Dynamically generates Backstage-compatible YAML entities for the BaselithCore instance and all active plugins.
2. **Pattern Detection System**: Automatically scans plugin source code to identify and tag [Agentic Design Patterns](../../architecture/agentic-patterns.md) implemented in the code.
3. **Software Templates**: A pre-configured Backstage Software Template for consistent and governed plugin creation.

---

## 2. The Entity Provider

The BaselithCore framework acts as a dynamic entity provider for Backstage. Instead of manually maintaining `catalog-info.yaml` files for every plugin, the framework exposes a dedicated API endpoint that returns the current state of the platform in Backstage's expected format.

### Endpoints

- **Catalog Entities**: `GET /api/backstage/catalog-entities`
  Returns a collection of `Component` and `Resource` entities representing the core system and individual plugins.
- **Software Template**: `GET /api/backstage/software-template.yaml`
  Returns the scaffolding template configuration for use in Backstage.

### Configuration in Backstage

To integrate BaselithCore with your Backstage instance, add the following to your Backstage `app-config.yaml`:

```yaml
catalog:
  locations:
    - type: url
      target: https://your-baselith-instance.com/api/backstage/catalog-entities
      rules:
        - allow: [Component, Resource, Template]
```

---

## 3. Automated Pattern Detection

One of the most advanced features of the integration is the **Agentic Pattern Detection System**. When exporting entities to Backstage, the framework performs a non-blocking asynchronous scan of the plugin's source code.

### How it works

The system checks for specific imports and class signatures that indicate the use of core framework utilities. Based on these findings, it automatically applies "well-known" annotations and tags to the Backstage entities.

| Detected Pattern | Detection Rule (Imports/Keywords) | Backstage Tag |
| :--- | :--- | :--- |
| **Reasoning** | `core.reasoning` | `baselith.ai/pattern-reasoning` |
| **Reflection** | `core.reflection` | `baselith.ai/pattern-reflection` |
| **Memory** | `core.memory`, `core.hierarchical_memory` | `baselith.ai/pattern-memory` |
| **Prioritization** | `core.prio` | `baselith.ai/pattern-prio` |
| **Planning** | `core.planning` | `baselith.ai/pattern-planning` |

This enables a high-level overview of capabilities across the entire multi-agent ecosystem directly from the developer portal.

---

## 4. Software Templates

BaselithCore includes a standard **Software Template** that allows developers to scaffold new plugins without leaving the Backstage UI.

### Scaffolding Flow

1. **Selection**: Choose the "Baselith Plugin Template" from the Backstage Create page.
2. **Input**: Provide the plugin name, description, and author information.
3. **Scaffolding**: Backstage uses the Baselith skeleton to generate the plugin structure.
4. **Publishing**: The plugin is automatically committed to your repository and registered in the catalog.

### Benefits

- **Standardization**: Ensures all plugins follow the framework's modular and asynchronous architecture.
- **Speed**: One-click generation of the `manifest.yaml`, `plugin.py`, and `agent.py`.
- **Governance**: Centralized management of plugin creation and naming conventions.

---

## 5. Metadata Mapping

The framework maps Baselith metadata to Backstage fields as follows:

- `name`: `metadata.name` — Slugified name
- `description`: `metadata.description` — Full text summary
- `tags`: `metadata.tags` — Merged with detected patterns
- `author`: `spec.owner` — Maps to the authoring entity
- `version`: `metadata.annotations['baselith.ai/version']`

!!! tip "Custom Metadata"
    Any custom fields added to yours plugin's `manifest.yaml` (under the `extra` section) will be automatically included as annotations in the Backstage entity.
