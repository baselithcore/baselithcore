# Getting started

From a fresh checkout to your first autonomous browser run.

## 1. Install dependencies

```bash
pip install playwright>=1.45.0 playwright-stealth>=1.0.6 httpx>=0.27.0 psutil>=5.9.0
playwright install chromium
```

Computer Use is opt-in and pulls extra dependencies only if you plan to use
it:

```bash
pip install "pyautogui>=0.9.54" "mss>=9.0.1" "Pillow>=10.0.0"
```

!!! note "Platform notes for Computer Use"
    - **macOS** — grant **Accessibility** and **Screen Recording** to your
      Python interpreter in *System Settings → Privacy & Security*.
    - **Linux headless** — run inside `Xvfb :99 -screen 0 1280x720x24` or a
      VNC-backed VM.
    - **Windows** — works out of the box via the standard `pyautogui` backend.

## 2. Build the React dashboard

```bash
cd plugins/baselithbot/ui
npm install
npm run build      # emits plugins/baselithbot/ui/dist
```

`ui/dist/**/*` is packaged data (`pyproject.toml`), so a normal
`pip install baselith-core` ships the built bundle. Without a build, the
mount at `/baselithbot/ui/` serves a self-diagnosing 503 page with the exact
build command instead of an opaque 404 — see
[Security & RBAC](reference/security.md) for why that matters.

## 3. Enable the plugin

Interactive wizard:

```bash
baselith baselithbot onboard --write
```

Or edit `configs/plugins.yaml` directly:

```yaml
baselithbot:
  enabled: true
  headless: true
  max_steps: 20
```

## 4. Set the dashboard token

Every dashboard *write* endpoint (and `POST /baselithbot/run`) is guarded by
a bearer token:

```bash
export BASELITHBOT_DASHBOARD_TOKEN=$(openssl rand -hex 32)
```

Without it the plugin runs in **open dev mode** — reads and writes pass, with
a one-shot warning logged (`baselithbot_dashboard_open`). Fine for a laptop,
never for a shared network. See [Security & RBAC](reference/security.md).

## 5. Start the backend

```bash
python backend.py                          # or: baselith serve
baselith baselithbot gateway --port 8000    # equivalent, plugin-scoped alias
baselith doctor                             # environment probe
```

Open **[http://localhost:8000/baselithbot/](http://localhost:8000/baselithbot/)**
— you land on the [Overview](guide/overview.md) page.

## 6. Run your first task

From the dashboard's [Run Task](guide/run-task.md) page, or directly:

```bash
curl -X POST http://localhost:8000/baselithbot/run \
  -H "Authorization: Bearer $BASELITHBOT_DASHBOARD_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"goal": "open duckduckgo homepage", "max_steps": 3}'
```

Or via the CLI:

```bash
baselith baselithbot run "open duckduckgo homepage" --max-steps 3
```

Watch it execute step-by-step on the **Run Task** page (SSE-driven, no
refresh needed), then inspect the recorded run frame-by-frame on
[Replay](guide/replay.md).

## 7. Verify

```bash
baselith baselithbot status
```

Production checklist (before exposing beyond localhost): dashboard token
set, UI bundle built, Chromium installed with `--with-deps`, reverse proxy
configured for WebSocket + SSE — see the
[Operations runbook](operations/runbook.md).

## Next steps

- Wire up messaging: [Channels](guide/channels.md).
- Turn on OS-level control safely: [Computer Use](guide/computer-use.md) +
  [Approvals](guide/approvals.md).
- Pick your LLM/vision providers: [Models](guide/models.md).
- Understand what's tenant-scoped and what isn't:
  [Multi-tenancy](reference/tenancy.md).
