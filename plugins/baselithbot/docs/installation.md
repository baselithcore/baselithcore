# Installation

[← Index](./README.md)

## 1. Core dependencies (always required)

```bash
pip install playwright>=1.45.0 playwright-stealth>=1.0.6 httpx>=0.27.0 psutil>=5.9.0
playwright install chromium
```

## 2. Computer Use dependencies (opt-in only)

```bash
pip install "pyautogui>=0.9.54" "mss>=9.0.1" "Pillow>=10.0.0"
```

Platform notes:

- **macOS** — grant **Accessibility** + **Screen Recording** permission to
  your Python interpreter in *System Settings → Privacy & Security*.
- **Linux headless** — run inside `Xvfb :99 -screen 0 1280x720x24` or a
  VNC VM.
- **Windows** — supported via the standard `pyautogui` backend; no extra
  steps.

## 3. Build the React dashboard

```bash
cd plugins/baselithbot/ui
npm install
npm run build          # emits plugins/baselithbot/ui/dist
```

`ui/dist/**/*` declared in [`pyproject.toml`](../../../pyproject.toml)
under `[tool.setuptools.package-data]`, so `pip install baselith-core`
ships the built bundle automatically.

Dev server (with API proxy to FastAPI on `:8000`):

```bash
npm run dev            # http://localhost:5180
```

## 4. Enable the plugin

Edit [`configs/plugins.yaml`](../../../configs/plugins.yaml):

```yaml
baselithbot:
  enabled: true
  headless: true
  max_steps: 20
```

Start the backend:

```bash
python backend.py              # or: baselith serve
baselith doctor                # environment probe
```

Open [http://localhost:8000/baselithbot/](http://localhost:8000/baselithbot/).

## 5. Verify

```bash
# Plugin status
baselith baselithbot status

# End-to-end smoke test
curl -X POST http://localhost:8000/baselithbot/run \
  -H "Content-Type: application/json" \
  -d '{"goal": "open duckduckgo homepage", "max_steps": 3}'
```

## 6. Production install checklist

- [ ] `BASELITHBOT_DASHBOARD_TOKEN` exported (see [security.md](./security.md)).
- [ ] UI bundle built (`ui/dist/index.html` present).
- [ ] Chromium installed via `playwright install chromium --with-deps`.
- [ ] If Computer Use enabled: `filesystem_root` created, `audit_log_path`
      on append-only volume, `pyautogui.FAILSAFE` respected.
- [ ] Reverse proxy configured for WS + SSE (see [operations.md](./operations.md)).
- [ ] Observability sinks wired (Prometheus scrape, log shipper).
