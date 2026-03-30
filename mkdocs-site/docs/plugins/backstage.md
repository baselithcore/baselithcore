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

## 2. Security & Authentication

All Backstage integration endpoints are protected by the BaselithCore security layer. To access these endpoints via API, you must provide a valid **API Key** or **Bearer Token** with `admin` or `job` permissions.

### Authorization Header

Include the following header in your requests:

```text
Authorization: ApiKey <YOUR_ADMIN_KEY>
```

### Example Test with curl

Use the `-v` flag to debug the connection if you receive no output:

```bash
curl -v -H "Authorization: ApiKey secret-admin-key" \
  http://localhost:8000/api/backstage/entities
```

---

The BaselithCore framework provides an automated integration for Backstage. The recommended approach for registering plugins into the catalog is using static `file` locations pointing to `catalog-info.yaml` files within each plugin's directory.

### 1. Create catalog-info.yaml

In your plugin directory (e.g., `plugins/my-plugin/`), create a `catalog-info.yaml` file with the following structure:

```yaml title="plugins/my-plugin/catalog-info.yaml"
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: my-plugin
  title: My Awesome Plugin
  description: A short description of what my plugin does
  annotations:
    backstage.io/techdocs-ref: dir:.
    backstage.io/source-location: url:https://baselithcore.xyz
    baselith.ai/plugin-api-url: http://localhost:8000/api/plugins/my-plugin
    baselith.ai/health-url: http://localhost:8000/health
  links:
    - url: https://baselithcore.xyz
      title: BaselithCore Website
      icon: web
  tags:
    - ai
    - agent
  labels:
    baselith.ai/readiness: stable
    baselith.ai/category: generic
spec:
  type: service
  lifecycle: production
  owner: group:default/guests
  system: baselithcore
```

### 2. Define the System Entity

If not already defined elsewhere, you should include the `System` entity in one of your catalog files to resolve relationships:

```yaml
apiVersion: backstage.io/v1alpha1
kind: System
metadata:
  name: baselithcore
  title: BaselithCore
  description: The BaselithCore multi-agent framework
spec:
  owner: group:default/guests
```

### 3. Register in Backstage

Update your Backstage `app-config.yaml` to include the plugin's catalog file:

```yaml title="backstage-portal/app-config.yaml"
catalog:
  locations:
    - type: file
      target: ../../../plugins/my-plugin/catalog-info.yaml
      rules:
        - allow: [Component, Resource, System]
```

> [!NOTE]
> The `target` path is relative to the `backstage-portal/packages/backend/` directory if running the local dev portal.

---

## 3. Automated Pattern Detection

One of the most advanced features of the integration is the **Agentic Pattern Detection System**. When exporting entities to Backstage, the framework performs a non-blocking asynchronous scan of the plugin's source code.

### How it works

The system checks for specific imports and class signatures that indicate the use of core framework utilities. Based on these findings, it automatically applies "well-known" annotations and tags to the Backstage entities.

| Detected Pattern | Detection Rule (module import) | Backstage Label |
| :--- | :--- | :--- |
| **Reasoning** | `core.reasoning` | `baselith.ai/pattern-reasoning` |
| **Reflection** | `core.reflection` | `baselith.ai/pattern-reflection` |
| **Planning** | `core.planning` | `baselith.ai/pattern-planning` |
| **Guardrails** | `core.guardrails` | `baselith.ai/pattern-guardrails` |
| **Swarm** | `core.swarm` | `baselith.ai/pattern-swarm` |
| **Agent-to-Agent (A2A)** | `core.a2a` | `baselith.ai/pattern-a2a` |
| **Human-in-the-Loop** | `core.human` | `baselith.ai/pattern-human-in-the-loop` |
| **MCP** | `core.mcp` | `baselith.ai/pattern-mcp` |
| **World Model** | `core.world_model` | `baselith.ai/pattern-world-model` |
| **Exploration** | `core.exploration` | `baselith.ai/pattern-exploration` |
| **Adversarial** | `core.adversarial` | `baselith.ai/pattern-adversarial` |
| **Personas** | `core.personas` | `baselith.ai/pattern-personas` |
| **Meta-Agent** | `core.meta` | `baselith.ai/pattern-meta-agent` |
| **Learning** | `core.learning` | `baselith.ai/pattern-learning` |
| **Fine-tuning** | `core.finetuning` | `baselith.ai/pattern-finetuning` |
| **Memory Tiering** | `core.memory` | `baselith.ai/pattern-memory-tiering` |
| **Evaluation** | `core.evaluation` | `baselith.ai/pattern-evaluation` |
| **Task Queue** | `core.task_queue` | `baselith.ai/pattern-task-queue` |
| **Goals** | `core.goals` | `baselith.ai/pattern-goals` |
| **Orchestration** | `core.orchestration` | `baselith.ai/pattern-orchestration` |
| **Knowledge Graph** | `core.graph` | `baselith.ai/pattern-knowledge-graph` |
| **Multi-Tenancy** | `core.context` | `baselith.ai/pattern-multi-tenancy` |

This enables a high-level overview of capabilities across the entire multi-agent ecosystem directly from the developer portal.

> [!TIP]
> Detection is cached securely at the framework level and invalidated only during hot-reloading of the affected plugin.

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

---

## 6. Local Development (Monorepo)

The BaselithCore repository includes a pre-configured Backstage portal in the `backstage-portal/` directory. This allows you to test and develop your agentic ecosystem with a professional developer portal locally.

### Prerequisites

- **Node.js**: 18.x or 20.x
- **Yarn**: 1.22.x (v1)

### Running the Portal

1. **Navigate to the portal directory**:

   ```bash
   cd backstage-portal
   ```

2. **Install dependencies**:

   ```bash
   yarn install
   ```

3. **Start the dev server**:

   ```bash
   yarn start
   ```

The portal will be available at [http://localhost:3000](http://localhost:3000).

### Connecting to BaselithCore

Ensure your BaselithCore instance is running (default: `http://localhost:8000`). The local portal is pre-configured to proxy requests to this address using the `ApiKey` defined in your `.env`.

> [!IMPORTANT]
> To see your plugins in the catalog, ensure they are enabled in `configs/plugins.yaml`, they have a valid `catalog-info.yaml`, and that the location is registered in `app-config.yaml`.

---

## 7. Metadata Mapping

The framework maps Baselith metadata to Backstage fields as follows:

- `name`: `metadata.name` — Slugified name
- `description`: `metadata.description` — Full text summary
- `tags`: `metadata.tags` — Merged with detected patterns
- `author`: `spec.owner` — Maps to the authoring entity
- `version`: `metadata.annotations['baselith.ai/version']`

---

## 8. Marketplace Alignment

To ensure that your plugin is correctly identified across both your internal Backstage portal and the **Official Baselith Marketplace**, use the following metadata mapping:

| Backstage Field | Marketplace `manifest.yaml` | Example / Value |
| :--- | :--- | :--- |
| `metadata.name` | `name` (slug) | `my-agent-plugin` |
| `metadata.title` | `name` (display) | `My Agent Plugin` |
| `metadata.description` | `description` | `Summary of capabilities...` |
| `spec.owner` | `author` | `group:default/guests` |
| `links` | `homepage` | `https://baselithcore.xyz` |

### Standard Categories

Use the following values for `baselith.ai/category` to match the Marketplace categorisation:

- `agent`: Full AI agent implementations
- `tool`: Specialized tools for agents (calculators, searchers)
- `interface`: UI components and widgets
- `workflow`: Pre-defined multi-step processes
- `generic`: Utility or infrastructural plugins

> [!TIP]
> The **Marketplace Hub** service (`baselithcore-marketplace-plugin`) also ships with its own `catalog-info.yaml`,
> making it discoverable as a `generic` infrastructure component within Backstage just like any other plugin.
> This allows you to monitor its health, lifecycle, and ownership from the same portal.
