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
3. **Marketplace submission (required)** — hub URL, bearer-token secret name, framework host serving `/api/backstage/publish`.
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

## Required secrets

| Secret                         | Scope                | Consumer                                                          |
| ------------------------------ | -------------------- | ----------------------------------------------------------------- |
| `BASELITH_MARKETPLACE_TOKEN`   | Backstage (required) | Passed as bearer to `POST /api/backstage/publish`                 |
| `GITHUB_TOKEN` (auto-provided) | optional CI          | Used only when `mirrorToGithub=true` and later GH-Action releases |

Declare the marketplace token in `app-config.yaml`:

```yaml
integrations:
  secrets:
    - name: BASELITH_MARKETPLACE_TOKEN
      value: ${MARKETPLACE_JWT}
```

## Running the flow for baselithbot

1. Open Backstage → **Create** → **Publish BaselithCore Plugin**.
2. Form values (minimum):
   - `pluginName`: `baselithbot`
   - `sourceRepoUrl`: `https://github.com/baselithcore/baselithcore-prod`
   - `sourcePath`: `plugins/baselithbot`
   - `version`: `1.0.0`
   - `license`: `MIT`
   - `entryPoint`: `plugin:BaselithbotPlugin`
   - `marketplaceUrl`: `https://marketplace.baselithcore.xyz`
   - `authTokenSecretName`: `BASELITH_MARKETPLACE_TOKEN`
3. Click **Create**. The Scaffolder pipeline runs end-to-end:
   - fetches + overlays the plugin,
   - POSTs the bundle to `/api/backstage/publish` on the framework host,
   - framework submits to the marketplace hub.
4. PENDING listing appears on `marketplace.baselithcore.xyz`; admin
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
