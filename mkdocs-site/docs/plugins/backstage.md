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

1. **Backstage Entity Provider**: Dynamically generates a *complete, valid entity graph* — the `baselith-core` `System`, one `Component` per active plugin, and one `API` entity per plugin that exposes routers — so no catalog reference ever dangles.
2. **Pattern Detection System**: Automatically scans plugin source code to identify and tag [Agentic Design Patterns](../../architecture/agentic-patterns.md) implemented in the code.
3. **Software Templates**: A pre-configured Backstage Software Template for consistent and governed plugin creation.

---

## 2. Security & Authentication

All Backstage integration endpoints are protected by the BaselithCore security layer. To access these endpoints you must provide a valid credential with `admin` or `job` permissions.

### Authorization Header

Using an API key:

```text
Authorization: ApiKey <YOUR_ADMIN_KEY>
```

Using a Bearer token:

```text
Authorization: Bearer <YOUR_JWT_TOKEN>
```

### Example Test with curl

```bash
curl -v -H "Authorization: ApiKey secret-admin-key" \
  http://localhost:8000/api/backstage/entities
```

---

## 3. Catalog Registration — Live Entity Provider

**The recommended (and default) integration is the live Entity Provider**: the
portal's `BaselithCoreEntityProvider` polls `GET /api/backstage/entities` and
ingests the **complete entity graph** — Domain, System, owner Groups, shared
Resources, one Component per plugin, and one API entity per plugin that
exposes routers or a mounted FastAPI sub-app — directly from the running
framework. There is nothing to
author or maintain per plugin: the catalog is generated from each plugin's
`manifest.yaml` and live registry state, so it can never drift from reality.

```ts title="backstage-portal/packages/backend/src/providers/baselith-core.ts"
// Polls /api/backstage/entities (ETag-aware) and applies a "full" mutation.
catalog.addEntityProvider(new BaselithCoreEntityProvider({ baseUrl, apiKey, ... }));
```

Do **not** also register static `catalog-info.yaml` file locations for
plugins: the provider already emits those entities, and the same entity name
arriving from two locations is a conflict in Backstage.

### How plugin metadata maps to the Component entity

| Manifest / runtime source | Catalog destination |
| :--- | :--- |
| `name` | `metadata.name` (format-sanitised; raw id kept in `baselith.ai/plugin-id`) |
| `description` | `metadata.description` |
| `author` | `spec.owner` → `group:default/<slug>` (the Group entity is emitted too) |
| `readiness` | `spec.lifecycle` (`stable/ga/production` → `production`, `deprecated` → `deprecated`, else `experimental`) + `baselith.ai/readiness` label |
| live `PluginState` | `baselith.ai/runtime-state` label (`active`, `failed`, `disabled`, …) |
| `version` | `app.kubernetes.io/version` label |
| `tags` + `category` | `metadata.tags` + `baselith.ai/category` label |
| `plugin_dependencies` | `spec.dependsOn` → `component:default/<dep>` |
| `required_resources` | `spec.dependsOn` → `resource:default/<res>` (Resource entities emitted, typed: `postgres` → `database`, `redis` → `cache`, `qdrant` → `vector-database`, `llm` → `llm-provider`, …) |
| `optional_resources` | `baselith.ai/optional-resources` annotation (not hard dependencies) |
| routers | `spec.providesApis` → `api:default/<plugin>-api` |
| `homepage` | `metadata.links` entry |
| `BASELITH_PLUGIN_LINK_TEMPLATE` env (optional, `{plugin}` placeholder) | "Manage Plugin" link (browser-renderable; machine endpoints stay in annotations) |
| `BASELITH_DOCS_URL` env (optional) | "Documentation" link (omitted when unset — no broken links) |
| repo layout | `backstage.io/source-location` → `<catalog-source-location>/plugins/<name>/` |
| `mkdocs.yml` present in plugin dir | `backstage.io/techdocs-ref` (omitted otherwise, so the Docs tab is never broken) |

> [!NOTE]
> `spec.lifecycle` is **maturity** (from the manifest `readiness`), per
> Backstage convention. Live operational health is exported separately as the
> `baselith.ai/runtime-state` label — the two are never conflated.

### Static catalog-info.yaml (optional, for external catalogs only)

A static file is only useful when a plugin's entity must be discoverable by a
Backstage instance that cannot reach a running framework (e.g. GitHub-based
discovery of a standalone plugin repo). In that case, generate the file from
the live entity instead of writing it by hand, so it matches what the
provider would emit:

```bash
curl -H "Authorization: ApiKey $BASELITH_API_KEY" \
  http://localhost:8000/api/backstage/entities/my-plugin | yq -P > catalog-info.yaml
```

---

## 4. Automated Pattern Detection

One of the most advanced features of the integration is the **Agentic Pattern Detection System**. When exporting entities to Backstage, the framework performs a non-blocking asynchronous scan of the plugin's source code.

### How it works

Detection runs three complementary strategies in order, merging results:

1. **Tag-based** (zero I/O): intersects `manifest.yaml` tags against known pattern aliases. A tag `"reasoning"` resolves to `baselith.ai/pattern-reasoning`.
2. **Resource-based** (zero I/O): maps `required_resources` / `optional_resources` to known patterns. For example, declaring `llm` as a resource implies `baselith.ai/pattern-reasoning`.
3. **Source-scan** (async, non-blocking): scans `.py` files in the plugin directory for `from core.X` / `import core.X` import statements, executed in an executor thread to avoid blocking the event loop.

### Detected Patterns

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

### Resource-to-Pattern shortcuts

| Resource name | Implied Pattern Label |
| :--- | :--- |
| `llm` | `baselith.ai/pattern-reasoning` |
| `evaluation` | `baselith.ai/pattern-evaluation` |
| `vectorstore` | `baselith.ai/pattern-memory-tiering` |

This enables a high-level overview of capabilities across the entire multi-agent ecosystem directly from the developer portal.

> [!TIP]
> Detection results are cached at the framework level and invalidated automatically when a plugin is hot-reloaded. The cache is pre-warmed at startup via lifecycle hooks — the first catalog export is always instant.

---

## 5. API Endpoints

The framework exposes seven REST endpoints under `/api/backstage`. All require `admin` or `job` credentials.

### `GET /api/backstage/entities`

Returns the **complete entity graph** (Domain + System + Groups + Resources + Components + APIs) compatible with Backstage's `EntityProvider.applyMutation()` "full" mutation contract.

```json
{
  "type": "full",
  "entities": [ ... ]
}
```

Designed to be polled by a Backstage `CustomEntityProvider` or a scheduled sync job.

The endpoint supports **conditional requests**: every response carries a weak `ETag` computed over the canonical payload (orjson bytes with sorted keys — the same bytes served as the body, so the payload is serialised exactly once), and a request with a matching `If-None-Match` header returns `304 Not Modified` with no body. Pollers should store the last `ETag` and send it back — an unchanged catalog then costs a header round-trip instead of a full re-serialisation. Upgrading the framework across the orjson switch changes the ETag encoding once, costing a single full re-sync.

#### Emitted entity kinds

| Kind | Cardinality | Purpose |
| :--- | :--- | :--- |
| `Domain` | 1 (`baselith`) | Platform root; the System attaches via `spec.domain`. |
| `System` | 1 (`baselith-core`) | Root the Components attach to via `spec.system`. |
| `Group` | one per unique owner | Backs every `spec.owner` reference (platform team + manifest authors) so no owner ref dangles. |
| `Resource` | one per unique `required_resources` id | Shared infrastructure (database, cache, vector-database, llm-provider, …) that Components `dependsOn`. |
| `Component` | one per registered plugin | `spec.type: baselith-plugin`; carries pattern labels, runtime-state label, health/docs annotations. |
| `API` | one per plugin with routers **or** a mounted FastAPI sub-app | `spec.type: openapi`. When the framework wires an OpenAPI supplier (the default in `lifespan.py`), `spec.definition` embeds an **inline OpenAPI document scoped to the plugin's route prefix** (with transitively pruned `components`); otherwise it falls back to a `$text` reference to `/openapi.json`. |

Some plugins expose their HTTP API not through host routers but by mounting a self-contained FastAPI sub-application (`app.mount(mount_path, get_app(), name="<plugin>")`). Those routes live in a separate ASGI app, invisible to `get_routers()` and the host `/openapi.json`, so the route-slicing path above cannot see them. The exporter closes this gap: when the Entity Provider endpoint is polled it passes the host app's routes to the graph assembler, which discovers every `Mount` whose mounted app exposes its own `openapi()` and, **keyed by the mount `name` matching the plugin's registry name**, emits an `API` entity whose `spec.definition` inlines the sub-app's OpenAPI with each path re-prefixed by the mount path (so the contract stays addressable at the URL it is actually served from). Static-file / SPA mounts have no `openapi()` and are skipped. For this attribution to work the mount `name` **must** equal the plugin's registry name.

Every emitted entity carries the `backstage.io/managed-by-location` and `backstage.io/managed-by-origin-location` annotations (pointing at this endpoint), which Backstage requires for provider-ingested entities.

### `GET /api/backstage/entities/{plugin_name}`

Returns the `catalog-info.yaml` entity dict for a single plugin. The response body is valid YAML-serialisable content for a `catalog-info.yaml` file. Returns `404` if the plugin is not found.

### `GET /api/backstage/entities/{plugin_name}/patterns`

Returns only the detected Agentic Design Pattern labels for a plugin as a JSON array of strings. Cached after first call; invalidated on hot-reload.

```json
["baselith.ai/pattern-reasoning", "baselith.ai/pattern-planning"]
```

### `GET /api/backstage/health`

Returns the operational status of the Backstage exporter and the count of registered plugins.

```json
{
  "status": "ok",
  "exporter": "BackstageProvider",
  "registered_plugins": 5,
  "entity_provider_endpoint": "/api/backstage/entities"
}
```

### `GET /api/backstage/software-template.yaml`

Returns the standard Baselith plugin scaffolding template YAML (`Content-Type: application/x-yaml`). Returns `404` if the template file is not found in the framework installation.

### `GET /api/backstage/publish-template.yaml`

Returns the Backstage Scaffolder template that submits an existing plugin directly to the marketplace hub. See [Backstage Publish](backstage-publish.md) for the full form + pipeline description.

### `POST /api/backstage/publish`

Thin wrapper around `core.marketplace.publisher.PluginPublisher.publish`. Consumed by the publish Scaffolder template via the `http:backstage:request` action. Request body:

```json
{
  "plugin_path": "/abs/path/to/plugin",
  "auth_token": "<jwt>"
}
```

Returns the submission result (hub status, plugin id, version). `502` on hub-side errors.

!!! warning "Security posture"
    - `registry_url` is **deprecated and ignored**: both the GitHub-token
      exchange and the submission always target the framework's
      `OFFICIAL_MARKETPLACE_URL`. Honoring a caller-supplied hub URL would let
      a job-role key redirect the forwarded GitHub token (SSRF / credential
      exfiltration).
    - Set `PLUGIN_PUBLISH_WORKSPACE_ROOT` to confine `plugin_path` to the
      Backstage Scaffolder workspace mount; paths outside it are rejected with
      `403`. Unset, any host path is accepted (legacy behavior).

---

## 6. Software Templates

BaselithCore ships two Scaffolder templates out of the box:

| Template                    | Purpose                                                        | Endpoint                                      |
| --------------------------- | -------------------------------------------------------------- | --------------------------------------------- |
| `baselith-plugin-template`  | Scaffold a **new** plugin inside a target GitHub repository.   | `GET /api/backstage/software-template.yaml`   |
| `baselith-plugin-publish`   | **Publish** an existing plugin directly to the marketplace hub. | `GET /api/backstage/publish-template.yaml`    |

### Scaffolding Flow (create)

1. **Selection**: Choose the "Baselith Plugin Template" from the Backstage Create page.
2. **Input**: Provide the plugin name, description, and author information.
3. **Scaffolding**: Backstage uses the Baselith skeleton to generate the plugin structure.
4. **Publishing**: The plugin is automatically committed to your repository and registered in the catalog.

### Publishing Flow (release)

1. **Selection**: Choose "Publish BaselithCore Plugin to Marketplace" from the Create page.
2. **Input**: Source monorepo URL, plugin path, version, license, bearer-token secret name.
3. **Pipeline**: `fetch:plain` + `fetch:template` overlay + `http:backstage:request` → `POST /api/backstage/publish` → marketplace hub.
4. *(optional)* Mirror the scaffolded bundle to a dedicated GitHub repo with CI.

See [Backstage Publish](backstage-publish.md) for full walkthrough.

### Benefits

- **Standardization**: Ensures all plugins follow the framework's modular and asynchronous architecture.
- **Speed**: One-click generation *and* one-click publish.
- **Governance**: Centralized management of plugin creation, naming, and release submission.

---

## 7. Local Development (Monorepo)

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

The portal will be available at [http://localhost:3010](http://localhost:3010) (moved off Backstage's default `:3000` to avoid colliding with FalkorDB / Grafana, which also use `:3000`).

### Connecting to BaselithCore

The portal is configured to work in a hybrid mode. By default, it proxies browser requests to your BaselithCore instance through the Backstage backend, and the `BaselithCoreEntityProvider` connects directly (server-side) for catalog sync.

#### 1. Environment Configuration

Set the following environment variables before starting the portal. Both are used by the proxy (browser path) and the entity provider (server-side path):

```bash
export BASELITH_BASE_URL=http://localhost:8000
export BASELITH_API_KEY=your-secret-admin-key
```

These variables are consumed by `app-config.yaml` in two places:

```yaml title="backstage-portal/app-config.yaml"
proxy:
  endpoints:
    '/baselith-api':
      target: ${BASELITH_BASE_URL:-http://localhost:8000}   # browser → BaselithCore
      headers:
        Authorization: 'ApiKey ${BASELITH_API_KEY:-12345678}'

baselith:
  baseUrl: ${BASELITH_BASE_URL:-http://localhost:8000}       # entity provider (server-side)
  apiKey: ${BASELITH_API_KEY:-12345678}                      # entity provider auth
```

Both fall back to `http://localhost:8000` / `12345678` when the variables are not set, which is sufficient for local development against a default BaselithCore instance.

#### 2. Dynamic Plugin Discovery (Recommended)

BaselithCore supports **Dynamic Entity Ingestion**. The portal includes a pre-configured `BaselithCoreEntityProvider` that automatically polls `/api/backstage/entities` every 10 minutes and registers all active plugins in the catalog.

The provider is already wired in `packages/backend/src/index.ts`:

```typescript
import { baselithCoreModule } from './providers/baselith-core';
// ...
backend.add(baselithCoreModule);
```

It reads its configuration from the `baselith` block in `app-config.yaml` — no additional setup is required.

#### 3. Static Plugin Registration

Alternatively, you can manually register plugins using static `file` locations in `app-config.yaml`. This is useful for plugins that are not yet active or for TechDocs development:

```yaml title="app-config.yaml"
catalog:
  locations:
    - type: file
      target: ../plugins/my-plugin/catalog-info.yaml
```

> [!IMPORTANT]
> The `target` path in `app-config.yaml` is relative to the `backstage-portal/` root directory.

---

## 8. Production Deployment

When deploying the Backstage portal to a production environment, use `app-config.production.yaml`, which **overrides** the dev defaults without fallback values — all variables must be explicitly set.

### Required Environment Variables

| Variable | Description |
| :--- | :--- |
| `BASELITH_BASE_URL` | Full base URL of the production BaselithCore instance (e.g. `https://api.example.com`) |
| `BASELITH_API_KEY` | An API key with `admin` or `job` permissions on the BaselithCore side |
| `POSTGRES_HOST` | PostgreSQL hostname for the Backstage catalog database |
| `POSTGRES_PORT` | PostgreSQL port (typically `5432`) |
| `POSTGRES_USER` | PostgreSQL username |
| `POSTGRES_PASSWORD` | PostgreSQL password |

### Production Config Structure

`app-config.production.yaml` is automatically merged on top of `app-config.yaml` by Backstage at startup. It provides the production-grade overrides:

```yaml title="backstage-portal/app-config.production.yaml"
proxy:
  endpoints:
    '/baselith-api':
      target: ${BASELITH_BASE_URL}
      headers:
        Authorization: 'ApiKey ${BASELITH_API_KEY}'

baselith:
  baseUrl: ${BASELITH_BASE_URL}
  apiKey: ${BASELITH_API_KEY}
```

> [!WARNING]
> Do not use fallback values (e.g. `${BASELITH_API_KEY:-12345678}`) in `app-config.production.yaml`. A missing variable should cause a startup failure rather than silently use an insecure default.

---

## 9. Metadata Mapping

The `BackstageProvider` maps `PluginMetadata` fields to Backstage entity fields as follows:

| `PluginMetadata` field | Backstage target | Notes |
| :--- | :--- | :--- |
| `name` | `metadata.name` | Format-sanitised (charset `[a-zA-Z0-9-_.]`, max 63 chars); the raw registry name is preserved in the `baselith.ai/plugin-id` annotation |
| `description` | `metadata.description` | Full text summary |
| `tags` | `metadata.tags` | Merged with category tag |
| `author` | `spec.owner` | Emitted as a valid entity ref `group:default/<slug>` (any `<email>` suffix is dropped); falls back to `group:default/baselith-core-team` |
| `version` | `metadata.labels['app.kubernetes.io/version']` | Only if non-empty; label values are sanitised to the Kubernetes charset |
| `readiness` | `metadata.labels['baselith.ai/readiness']` | e.g. `stable`, `experimental` |
| `category` | `metadata.labels['baselith.ai/category']` | Kebab-cased |
| `homepage` | `metadata.annotations['backstage.io/source-location']` + `metadata.links` | Only if non-empty |
| `license` | `metadata.annotations['baselith.ai/license']` | Only if non-empty |
| `min_core_version` | `metadata.annotations['baselith.ai/min-core-version']` | Only if non-empty |
| `plugin_dependencies` | `spec.dependsOn` | Each dep as a fully-qualified `component:default/<name>` ref |
| `get_routers()` | `spec.providesApis` | `["{name}-api"]` if non-empty — backed by an emitted `API` entity of the same name. A mounted FastAPI sub-app (mount `name` = plugin name) is also detected and backed by an `API` entity even when `get_routers()` is empty. |
| Detected patterns | `metadata.labels['baselith.ai/pattern-*']` | Value is always `"true"` |
| `PluginState` | `spec.lifecycle` | See state-to-lifecycle table below |

All Components are emitted in the explicit `default` namespace (`metadata.namespace: default`), matching the fully-qualified refs above.

### Always-present annotations

| Annotation | Value |
| :--- | :--- |
| `backstage.io/managed-by-location` | `url:{base_url}/api/backstage/entities` |
| `backstage.io/managed-by-origin-location` | `url:{base_url}/api/backstage/entities` |
| `backstage.io/techdocs-ref` | `dir:./plugins/{name}` |
| `baselith.ai/plugin-id` | The plugin's raw registry name (may differ from the sanitised `metadata.name`) |
| `baselith.ai/health-url` | `{base_url}/health` |
| `baselith.ai/plugin-api-url` | `{base_url}/api/plugins/{name}` |
| `baselith.ai/manifest-url` | `{catalog_source_location}plugins/{name}/manifest.yaml` |

### PluginState → Backstage lifecycle

| Plugin State | Backstage lifecycle |
| :--- | :--- |
| `ACTIVE` | `production` |
| `LOADING`, `INITIALIZING`, `LOADED`, `DISCOVERED` | `experimental` |
| `DISABLED`, `FAILED`, `UNLOADING` | `deprecated` |
| Unknown | `unknown` |

---

## 10. Marketplace Alignment

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
