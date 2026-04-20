# Publishing to the Baselith Marketplace

[← Index](./README.md)

How to extract Baselithbot into its own git repository and publish it to
the [BaselithCore Marketplace](https://marketplace.baselithcore.xyz/)
hub.

> **One-click alternative** — the framework now ships a Backstage
> Scaffolder template (`baselith-plugin-publish`) that automates every
> step below. See
> [BaselithCore docs → Backstage Publish](https://docs.baselithcore.xyz/plugins/backstage-publish)
> for the recommended path. The manual workflow that follows remains
> supported as an escape hatch for Backstage-less environments.

## 1. Standalone repo layout

Baselithbot is self-contained under [`plugins/baselithbot/`](../).
Every subsystem — plugin core, channels, canvas, skills, voice,
dashboard, deploy artifacts — lives inside the directory. A marketplace
release requires the following files at the repo root:

| File                                | Status                         | Purpose                                                       |
| ----------------------------------- | ------------------------------ | ------------------------------------------------------------- |
| [`plugin.py`](../plugin.py)         | present                        | Marketplace-required plugin entry point                       |
| [`README.md`](../README.md)         | present                        | Marketplace-required documentation                            |
| [`manifest.yaml`](../manifest.yaml) | present (needs patch — see §3) | Plugin metadata                                               |
| `LICENSE`                           | **missing — add**              | Required. MIT is declared in `manifest.yaml`.                 |
| `requirements.txt`                  | **missing — add**              | Recommended. Mirrors `python_dependencies` + `baselith-core`. |
| `logo.png`                          | optional                       | 1:1 aspect-ratio icon.                                        |

## 2. Extract into a separate repo

```bash
cp -R /path/to/baselithcore-prod/plugins/baselithbot \
      /path/to/baselithcore-baselithbot-plugin
cd /path/to/baselithcore-baselithbot-plugin

# Clean compiled caches before git init
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -type d -name '.state' -exec rm -rf {} + 2>/dev/null
```

Add the missing `LICENSE` (MIT) and `requirements.txt`:

```text
playwright>=1.45.0
playwright-stealth>=1.0.6
pyautogui>=0.9.54
mss>=9.0.1
Pillow>=10.0.0
httpx>=0.27.0
psutil>=5.9.0
baselith-core>=2.0.0
```

`baselith-core>=2.0.0` is mandatory — the plugin imports `core.*`
symbols (e.g. [`plugin.py`](../plugin.py) → `core.observability.logging`,
`core.plugins`, `core.services.vision.service`). Installing the ZIP
without the framework will raise `ImportError` at load time.

## 3. Patch `manifest.yaml`

The marketplace validator inspects `id`, `entry_point`, and
`repository` explicitly. Append to the existing manifest:

```yaml
id: baselithbot
entry_point: plugin:BaselithbotPlugin
repository: https://github.com/<user>/baselithcore-baselithbot-plugin
```

Keep the existing `name`, `version`, `description`, `author`,
`category`, `tags`, `python_dependencies`, `plugin_dependencies`,
`readiness`, `license` fields untouched.

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
use `shell=False` + `# nosec B603`; dynamic-import shims in
[`channels/signal.py`](../channels/signal.py) and
[`channels/matrix.py`](../channels/matrix.py) were replaced with real
`import time`). Warning-class patterns (`httpx`, `threading`,
`while True:`) remain intentionally and do not block submission.

Run the bundled CLI against the checkout before submitting:

```bash
baselith marketplace validate /path/to/baselithcore-baselithbot-plugin
```

## 5. Initial commit + tag

```bash
cd /path/to/baselithcore-baselithbot-plugin
git init -b main
git add -A
git commit -m "feat: initial extraction of baselithbot plugin v1.0.0"
git tag v1.0.0
git remote add origin git@github.com:<user>/baselithcore-baselithbot-plugin.git
git push -u origin main --tags
```

## 6. Login + publish

```bash
baselith marketplace login --url https://marketplace.baselithcore.xyz
baselith marketplace publish /path/to/baselithcore-baselithbot-plugin
```

Publisher flow:

1. Re-runs local validation.
2. ZIPs the tree (dotfiles + `__pycache__` excluded).
3. Optionally signs the archive with
   `MARKETPLACE_PUBLISHER_PRIVATE_KEY_PATH`.
4. POSTs to `/api/marketplace/plugins/submit` with the session token.

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

## 8. Keeping the extracted repo in sync with `baselithcore-prod`

Two strategies for keeping Baselithbot under both roofs:

- **`git subtree split`** — preserves commit history:

  ```bash
  cd baselithcore-prod
  git subtree split -P plugins/baselithbot -b baselithbot-split
  git push /path/to/baselithcore-baselithbot-plugin baselithbot-split:main
  ```

- **Git submodule** — `plugins/baselithbot` inside prod becomes a
  submodule pointing at the marketplace repo. Simpler contract; loses
  inline edit-in-place ergonomics.

Pick one per Baselithbot's release cadence. Mixing the two will diverge
histories — do not switch mid-flight.

## 9. CI gates that still apply

Even as a standalone repo, the same typing + architecture gates must
keep passing against `plugins/baselithbot/` inside the upstream
framework clone:

- [`scripts/check_architecture_boundaries.py`](../../../scripts/check_architecture_boundaries.py)
- [`scripts/check_official_plugin_typing.py`](../../../scripts/check_official_plugin_typing.py)

Mirror these gates (or a subset) into the standalone repo's CI to
detect regressions earlier — `mypy plugin.py agent.py handlers.py ...`

- `ruff check .` is the minimum viable gate.
