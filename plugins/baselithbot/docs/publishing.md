# Publishing to the Baselith Marketplace

[← Index](./README.md)

How to extract Baselithbot into its own git repository and publish it to
the [BaselithCore Marketplace](https://marketplace.baselithcore.xyz/)
hub.

> **One-click alternative** — the framework now ships a Backstage
> Scaffolder template (`baselith-plugin-publish`) that automates every
> step below. Auth is **GitHub OAuth end-to-end** — the Scaffolder
> forwards your Backstage GitHub identity (`secrets.USER_OAUTH_TOKEN`)
> to the framework, which exchanges it via the marketplace's
> `POST /auth/github/exchange` endpoint for a JWT bound to the same
> GitHub login that powers `marketplace.baselithcore.xyz/auth/login/github`.
> No static marketplace token secret is required. See
> [BaselithCore docs → Backstage Publish](https://docs.baselithcore.xyz/plugins/backstage-publish)
> for the recommended path. The manual workflow that follows remains
> supported as an escape hatch for Backstage-less environments.

## 1. Standalone repo layout

Baselithbot is self-contained under [`plugins/baselithbot/`](../).
Every subsystem — plugin core, channels, canvas, skills, voice,
dashboard, deploy artifacts — lives inside the directory. A marketplace
release requires the following files at the repo root:

| File                                                        | Status  | Purpose                                                                      |
| ----------------------------------------------------------- | ------- | ---------------------------------------------------------------------------- |
| [`plugin.py`](../plugin.py)                                 | present | Marketplace-required plugin entry point                                      |
| [`README.md`](../README.md)                                 | present | Marketplace-required documentation                                           |
| [`manifest.yaml`](../manifest.yaml)                         | present | Plugin metadata (`id`, `entry_point`, `min_core_version`, `icon`, …)         |
| [`LICENSE`](../LICENSE)                                     | present | AGPL-3.0-only (matches the core copyleft obligation of importing `core.*`). |
| [`requirements.txt`](../requirements.txt)                   | present | Mirrors `python_dependencies` + pins `baselith-core>=0.7.0,<1.0.0`.          |
| [`pyproject.toml`](../pyproject.toml)                       | present | PEP-621 package metadata for standalone builds + optional extras.            |
| [`CHANGELOG.md`](../CHANGELOG.md)                           | present | Keep-a-Changelog + SemVer; semantic-release consumes it.                     |
| [`SECURITY.md`](../SECURITY.md)                             | present | Threat model pointer + disclosure SLA.                                       |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md)                     | present | Ground rules + release flow.                                                 |
| [`CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md)               | present | Contributor Covenant 2.1.                                                    |
| [`logobg-baselithbot500.png`](../logobg-baselithbot500.png) | present | 500x500 RGBA icon (1:1 aspect ratio) declared in `manifest.yaml`.            |

## 2. Extract into a separate repo

```bash
cp -R /path/to/baselithcore/plugins/baselithbot \
      /path/to/plugin-baselithbot
cd /path/to/plugin-baselithbot

# Clean compiled caches before git init
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -type d -name '.state' -exec rm -rf {} + 2>/dev/null
```

`LICENSE`, `requirements.txt`, `pyproject.toml` and the release-hygiene
scaffolding already exist in the monorepo. If you are vendoring the
directory by hand for any reason, the minimum `requirements.txt` is:

```text
playwright>=1.45.0
playwright-stealth>=1.0.6
pyautogui>=0.9.54
mss>=9.0.1
Pillow>=10.0.0
httpx>=0.27.0
psutil>=5.9.0
baselith-core>=0.7.0,<1.0.0
```

`baselith-core>=0.7.0,<1.0.0` is mandatory — the plugin imports `core.*`
symbols (e.g. [`plugin.py`](../plugin.py) → `core.observability.logging`,
`core.plugins`, `core.services.vision.service`). Installing the ZIP
without the framework will raise `ImportError` at load time.

## 3. `manifest.yaml` — already marketplace-ready

`id`, `entry_point`, `repository`, `homepage`, `min_core_version`,
`icon`, and `license: AGPL-3.0-only` are already declared in the
shipped [`manifest.yaml`](../manifest.yaml). No patch step is needed
for an extraction that starts from the current monorepo state — if you
fork a URL, only update `repository:` to point at your fork.

## 3a. Build the dashboard bundle before packaging

The plugin ships a React dashboard under [`ui/`](../ui). Only the
compiled bundle in `ui/dist/` is packaged — `ui/src/` and
`ui/node_modules/` are excluded via
[`[tool.setuptools.exclude-package-data]`](../pyproject.toml) so the
wheel stays small (≈644 KB, 204 files). Regenerate `ui/dist/` before
every build:

```bash
cd plugins/baselithbot/ui
npm ci
npm run build
cd -
```

Verify the wheel locally:

```bash
python -m pip wheel --no-deps --no-build-isolation \
    plugins/baselithbot -w /tmp/baselithbot-wheel
python -m zipfile -l /tmp/baselithbot-wheel/*.whl | grep -c node_modules
# → 0 (node_modules must NOT be bundled)
```

If the count is non-zero the `exclude-package-data` block is broken —
stop and fix before publishing. Common cause: a stale `build/` or
`*.egg-info/` directory from a previous build; delete both, then retry.

## 4. Validator compliance

The hub runs a static scan on every submission. Categories:

- **Forbidden (auto-reject)** — shell-mode subprocess invocations,
  shell escape helpers, dynamic code-execution primitives, unsafe
  deserialization calls, archive-creation helpers, debugger
  breakpoints.
- **Warning (non-blocking)** — `requests.*`, `socket.*`,
  `http.client`, `urllib.request`, `base64.b64decode`, `threading.*`,
  `multiprocessing.*`, `while True:`, `itertools.cycle`.
- **Forbidden in `requirements.txt`** — stdlib names kept outside of
  dependency lists, plus `pyOpenSSL` and `cryptography`.

Baselithbot status: forbidden-pattern hits cleared (subprocess calls
use `shell=False` + `# noqa: S603` with justifications pointing at the
allowlist gate that feeds them; dynamic-import shims in
[`channels/signal.py`](../channels/signal.py) and
[`channels/matrix.py`](../channels/matrix.py) were replaced with real
`import time`). Warning-class patterns (`httpx`, `threading`,
`while True:`) remain intentionally and do not block submission.

Run the bundled CLI against the checkout before submitting:

```bash
baselith marketplace validate /path/to/plugin-baselithbot
```

## 5. Initial commit + tag

```bash
cd /path/to/plugin-baselithbot
git init -b main
git add -A
git commit -m "feat: initial extraction of baselithbot plugin v1.0.0"
git tag v1.0.0
git remote add origin git@github.com:<user>/plugin-baselithbot.git
git push -u origin main --tags
```

## 6. Login + publish

Two supported paths — both authenticate with GitHub, matching the
browser flow at `marketplace.baselithcore.xyz/auth/login/github`.

**Primary — Backstage Scaffolder (recommended).** Sign in to Backstage
with GitHub, open **Create → Publish BaselithCore Plugin**, submit the
form. The Scaffolder forwards your GitHub OAuth token via
`secrets.USER_OAUTH_TOKEN`; the framework exchanges it at the
marketplace's `POST /auth/github/exchange` endpoint. No static token
secret is required. Full walkthrough:
[Backstage Publish](https://docs.baselithcore.xyz/plugins/backstage-publish).

**Fallback — CLI (Backstage-less environments).**

```bash
baselith marketplace login --url https://marketplace.baselithcore.xyz
baselith marketplace publish /path/to/plugin-baselithbot
```

Publisher flow (shared by both paths):

1. Re-runs local validation.
2. ZIPs the tree (dotfiles + `__pycache__` excluded).
3. Optionally signs the archive with
   `MARKETPLACE_PUBLISHER_PRIVATE_KEY_PATH`.
4. POSTs to `/api/marketplace/plugins/submit` with the session token
   (JWT from the GitHub exchange, or — for legacy CI — a pre-issued
   admin key).

Submission enters `PENDING`. The hub runs a `Bandit` security scan —
high-severity hits auto-reject. Admins review, then the plugin appears
in Explore and in `/api/marketplace/plugins/registry.json`.

## 7. Release workflow (subsequent versions)

1. Bump `version:` in `manifest.yaml` (semver).
2. Update [`../DOCUMENTATION.md`](../DOCUMENTATION.md) changelog.
3. Tag + push: `git tag vX.Y.Z && git push origin main --tags`.
4. Re-run `baselith marketplace publish .`.

Approved versions coexist — the hub exposes each semver as a distinct
record, clients choose upgrade cadence.

## 8. Keeping the extracted repo in sync with `baselithcore`

**Canonical model — `git subtree split`.** The monorepo
(`baselithcore`) is the authoritative source of truth for
Baselithbot; the standalone repo
(`plugin-baselithbot`) is a derived publish target for
marketplace consumers. **All edits land in the monorepo first**, then
the subtree is split and pushed. This preserves commit history,
integration coverage (core version bumps exercise
`tests/plugins/baselithbot/` + `tests/unit/plugins_tests/test_baselithbot_*`
on every CI run), and the framework CI gates
(`scripts/check_official_plugin_typing.py`,
`scripts/check_architecture_boundaries.py`) that allowlist the plugin.

Publish cadence:

```bash
cd /path/to/baselithcore

# 1. Split a fresh branch reflecting the current monorepo HEAD.
git subtree split -P plugins/baselithbot -b baselithbot-split

# 2. Push to the standalone repo's main (force-with-lease — the split
#    rewrites commit SHAs; the standalone main is output-only, never
#    edited by hand).
git push --force-with-lease \
    git@github.com:<user>/plugin-baselithbot.git \
    baselithbot-split:main

# 3. Delete the throwaway split branch.
git branch -D baselithbot-split

# 4. In the standalone repo, tag + publish to the marketplace
#    (see §5–§6 above).
```

Golden rule: **never edit the standalone repo directly**. Any commit
landing there (outside the subtree push) will diverge and be overwritten
by the next `--force-with-lease`. Issue triage and PRs can live on the
standalone repo (marketplace-visible), but the fix merges into
`baselithcore` and re-propagates via subtree.

### Discouraged — Git submodule

Pointing `plugins/baselithbot` inside prod at an external submodule was
evaluated and rejected. The submodule dance (`git submodule update
--init`, detached HEAD edits, double commits) breaks the "edit in place"
ergonomics the monorepo depends on, and the framework CI gates
(`scripts/check_official_plugin_typing.py`) would have to be rewired to
clone the submodule before running. Do not switch strategies mid-flight
— mixing subtree and submodule will diverge histories.

## 9. CI gates that still apply

Even as a standalone repo, the same typing + architecture gates must
keep passing against `plugins/baselithbot/` inside the upstream
framework clone:

- [`scripts/check_architecture_boundaries.py`](../../../scripts/check_architecture_boundaries.py)
- [`scripts/check_official_plugin_typing.py`](../../../scripts/check_official_plugin_typing.py)

Mirror these gates (or a subset) into the standalone repo's CI to
detect regressions earlier — `mypy plugin.py agent.py handlers.py ...`

- `ruff check .` is the minimum viable gate.
