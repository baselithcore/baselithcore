# BaselithCore — Backstage Portal

A pre-configured [Backstage](https://backstage.io) developer portal for the BaselithCore multi-agent framework. It includes a custom Entity Provider that automatically ingests all active BaselithCore plugins into the Software Catalog, and a Software Template for scaffolding new plugins from the Backstage UI.

## Structure

```text
backstage-portal/
├── packages/
│   ├── app/                    # Backstage frontend (React)
│   └── backend/
│       └── src/
│           ├── index.ts        # Backend entry point — registers all plugins
│           └── providers/
│               └── baselith-core.ts  # BaselithCoreEntityProvider + module
├── app-config.yaml             # Dev configuration (env var fallbacks included)
└── app-config.production.yaml  # Production overrides (no fallbacks)
```

## Prerequisites

- Node.js 18.x or 20.x
- Yarn 1.22.x (v1)
- A running BaselithCore instance

## Running locally

```bash
# From the backstage-portal/ directory
yarn install
yarn start
```

The portal is available at <http://localhost:3010> (moved off Backstage's default :3000 to avoid colliding with FalkorDB / Grafana).

## Environment variables

| Variable | Dev default | Description |
| --- | --- | --- |
| `BASELITH_BASE_URL` | `http://localhost:8000` | URL of the BaselithCore instance |
| `BASELITH_API_KEY` | `12345678` | Admin or job API key for BaselithCore |
| `GITHUB_TOKEN` | — | GitHub PAT for catalog integrations |

In production, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` are also required for the catalog database.

## Dynamic catalog sync

`BaselithCoreEntityProvider` polls `GET /api/backstage/entities` every 10 minutes and applies a full mutation — all active BaselithCore plugins appear automatically in the catalog as `Component` entities with Agentic Design Pattern labels.

The provider is registered in `packages/backend/src/index.ts` and reads its config from `app-config.yaml`:

```yaml
baselith:
  baseUrl: ${BASELITH_BASE_URL:-http://localhost:8000}
  apiKey: ${BASELITH_API_KEY:-12345678}
```

See the [Backstage Integration docs](../mkdocs-site/docs/plugins/backstage.md) for the full reference.
