# Publishing plugins via Backstage

Modern publishing workflow that submits a plugin **directly to the
marketplace hub** (`marketplace.baselithcore.xyz`) via the Backstage
Scaffolder. Supersedes the manual `git init` / `baselith plugin
marketplace publish` loop documented in [`packaging.md`](./packaging.md)
and [`marketplace.md`](./marketplace.md).

> **Primary destination** — the marketplace hub. The template POSTs the
> packaged plugin directly to `/api/marketplace/plugins/submit` via the
> framework's own wrapper endpoint `POST /api/backstage/publish`. Creating
> a dedicated GitHub repository is **opt-in** (flag `mirrorToGithub`) and
> purely for source hosting / CI-driven re-releases.
> This page assumes your Backstage instance already consumes the BaselithCore
> exporter endpoints under `/api/backstage/*`. See [`backstage.md`](./backstage.md)
> for the base integration contract.

## Why Backstage

| Manual flow                                                       | Backstage flow                                                   |
| ----------------------------------------------------------------- | ---------------------------------------------------------------- |
| Extract + `git init` + `git tag` + `baselith marketplace publish` | Form submit → `POST /api/backstage/publish` → marketplace hub    |
| Hand-edit `manifest.yaml` with `id`, `entry_point`, `repository`  | `fetch:template` renders overlay                                 |
| Write `LICENSE`, `requirements.txt`                               | Skeleton ships them                                              |
| Manually track release artifacts                                  | Marketplace hub records PENDING → LIVE; catalog registers mirror |
| No audit trail                                                    | Backstage run log + marketplace moderation log                   |

## Template assets

All assets live under [`templates/backstage/`](../../templates/backstage/):

| File                                                         | Purpose                                                  |
| ------------------------------------------------------------ | -------------------------------------------------------- |
| `publish-template.yaml`                                      | Scaffolder Template — multi-step form + steps.           |
| `publish-skeleton/LICENSE`                                   | License placeholder (MIT / Apache-2.0 / AGPL-3.0 / BSD). |
| `publish-skeleton/requirements.txt`                          | Baseline deps (`baselith-core>=2.0.0`).                  |
| `publish-skeleton/manifest.overlay.yaml`                     | Fields merged into the plugin's manifest before release. |
| `publish-skeleton/.releaserc.json`                           | `semantic-release` config (conventional commits).        |
| `publish-skeleton/.github/workflows/marketplace-publish.yml` | Runs validator + publisher on tag push.                  |
| `publish-skeleton/RELEASE.md`                                | Runbook copied into the extracted repo.                  |

The template is also served via the exporter router at:

```bash
GET /api/backstage/publish-template.yaml
```

Register it in your Backstage `app-config.yaml` under the `catalog.locations`
section so the Scaffolder picks it up automatically:

```yaml
catalog:
  locations:
    - type: url
      target: https://baselithcore.xyz/api/backstage/publish-template.yaml
      rules:
        - allow: [Template]
```

## Scaffolder form steps

1. **Plugin source** — slug, monorepo URL, `sourcePath` (e.g. `plugins/baselithbot`).
2. **Release metadata** — version, license, entry point, author, readiness.
3. **Marketplace submission (required)** — hub URL + framework host serving `/api/backstage/publish`. **Auth is the signed-in user's GitHub identity** — the Scaffolder forwards `${{ secrets.USER_OAUTH_TOKEN }}` so no static marketplace token is required.
4. **Mirror to GitHub (optional)** — enable only if you want a dedicated repo + CI workflow alongside the marketplace listing.

## Execution pipeline

1. `fetch:plain` pulls the plugin dir into the Scaffolder workspace.
2. `fetch:template` renders the publish-skeleton overlay on top.
3. **`http:backstage:request` → `POST /api/backstage/publish`** — framework
   zips + submits the bundle to `{marketplaceUrl}/api/marketplace/plugins/submit`.
   This is the canonical submission path (wraps
   `core.marketplace.publisher.PluginPublisher.publish`).
4. *(optional)* `publish:github` mirrors the scaffolded bundle to a
   dedicated repo (only when `mirrorToGithub=true`).
5. *(optional)* `catalog:register` registers the mirror in the Backstage
   Software Catalog.

## Auth model — GitHub OAuth end-to-end

Publishing via Backstage uses the **same GitHub identity** as the browser
marketplace login at `marketplace.baselithcore.xyz/auth/login/github`.
Static marketplace JWT secrets are no longer required.

| Surface              | GitHub auth step                                                                          |
| -------------------- | ----------------------------------------------------------------------------------------- |
| Browser (marketplace) | `GET /auth/login/github` → OAuth redirect → `GET /auth/callback/github` → JWT cookie      |
| Backstage Scaffolder  | User signs in to Backstage with GitHub → `secrets.USER_OAUTH_TOKEN` forwarded to framework |
| Framework             | `POST /api/backstage/publish` → exchanges GH token via `POST /auth/github/exchange`       |
| Marketplace           | Validates GH token against `https://api.github.com/user`, issues JWT bound to GH login    |

Required configuration in Backstage `app-config.yaml`:

```yaml
auth:
  environment: production
  providers:
    github:
      production:
        clientId: ${AUTH_GITHUB_CLIENT_ID}
        clientSecret: ${AUTH_GITHUB_CLIENT_SECRET}

# Optional — only if also pushing a mirror repo with `mirrorToGithub=true`.
integrations:
  github:
    - host: github.com
      token: ${GITHUB_TOKEN}
```

Required configuration on the framework host:

```bash
# Drives exchange target for /api/backstage/publish.
BASELITHCORE_OFFICIAL_MARKETPLACE_URL=https://marketplace.baselithcore.xyz
```

Required configuration on the marketplace hub:

```bash
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
MARKETPLACE_MODE=server
```

Legacy clients may still POST `auth_token` (pre-issued JWT) or
`admin_key` to `/api/backstage/publish`; those paths are preserved for
CI systems that cannot reach Backstage, but new integrations should use
the GitHub flow.

## Running the flow for baselithbot

1. Open Backstage → **Create** → **Publish BaselithCore Plugin**.
2. Form values (minimum):
   - `pluginName`: `baselithbot`
   - `sourceRepoUrl`: `https://github.com/baselithcore/baselithcore`
   - `sourcePath`: `plugins/baselithbot`
   - `version`: `1.0.0`
   - `license`: `MIT`
   - `entryPoint`: `plugin:BaselithbotPlugin`
   - `marketplaceUrl`: `https://marketplace.baselithcore.xyz`
3. Ensure you are signed in to Backstage with GitHub — the Scaffolder forwards
   your GitHub OAuth token to the framework. No explicit token secret.
4. Click **Create**. The Scaffolder pipeline runs end-to-end:
   - fetches + overlays the plugin,
   - POSTs the bundle to `/api/backstage/publish` on the framework host,
   - framework submits to the marketplace hub.
5. PENDING listing appears on `marketplace.baselithcore.xyz`; admin
   approves → LIVE.

## Subsequent releases

Bump `version` in the Scaffolder form and run it again. Each submission
is an independent hub record; the hub preserves every semver side-by-side
so clients pick the upgrade cadence.

For automated semantic releases, flip `mirrorToGithub=true` on the first
run — the scaffolded `.releaserc.json` + `marketplace-publish.yml`
then re-publish on every `vMAJOR.MINOR.PATCH` tag pushed to the mirror.

## Fallback — local publish

If Backstage is offline or the repo is already extracted, run the CLI
equivalents locally:

```bash
baselith plugin marketplace validate .
baselith plugin marketplace login --url https://marketplace.baselithcore.xyz
baselith plugin marketplace publish .
```

The Scaffolder-driven flow is the preferred path, but the CLI remains a
supported escape hatch documented in [`marketplace.md`](./marketplace.md).
