# Operations — testing, deployment, troubleshooting, roadmap

[← Index](./README.md)

## 1. Testing

```bash
# Plugin-wide
python -m pytest tests/unit/plugins_tests/test_baselithbot_plugin.py -v

# Scoped
python -m pytest tests/unit/plugins_tests/ -k baselithbot
python -m pytest tests/unit/plugins_tests/ -k baselithbot -m "not slow"

# Coverage (project gate is 54%)
python -m pytest --cov=plugins/baselithbot --cov-report=html
```

Test doubles:

- `BrowserAgent` replaced with fake returning scripted `BrowserAction`s.
- `pyautogui`, `mss`, `psutil` monkeypatched.
- Vision + LLM services mocked at `core.services` boundary.
- Subprocess tests use `capfd` to assert argv vector (never string).

Strict typing gate (must pass for PRs):

```bash
python scripts/check_official_plugin_typing.py
python scripts/check_architecture_boundaries.py
```

## 2. Deployment recipes

### 2.1 Docker (headless)

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy
WORKDIR /app
COPY . .
RUN pip install -e ".[dev]" \
 && playwright install chromium
ENV BASELITHBOT_DASHBOARD_TOKEN=set-at-runtime
EXPOSE 8000
CMD ["python", "backend.py"]
```

### 2.2 Behind nginx

```nginx
location /baselithbot/ {
  proxy_pass         http://127.0.0.1:8000;
  proxy_http_version 1.1;
  proxy_set_header   Host $host;
  proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header   Upgrade $http_upgrade;
  proxy_set_header   Connection "upgrade";   # WS + SSE
  proxy_buffering    off;                    # SSE
  proxy_read_timeout 3600s;
}
```

Make sure FastAPI trusts the proxy headers for real-client-IP rate
limiting:

```python
from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
```

### 2.3 Systemd (VM target for Computer Use)

```ini
[Unit]
Description=Baselithbot agent node
After=network-online.target
Wants=network-online.target

[Service]
User=baselithbot
Environment="DISPLAY=:99"
Environment="BASELITHBOT_DASHBOARD_TOKEN=%I"
ExecStartPre=/usr/bin/Xvfb :99 -screen 0 1280x720x24
ExecStart=/usr/bin/python backend.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### 2.4 Environment hardening

| Aspect | Recommendation |
|--------|----------------|
| OS user | Dedicated, scoped `$HOME`, no sudo |
| Filesystem root | Disposable directory on ephemeral volume |
| Audit log | Append-only / WORM volume |
| Dashboard token | 32-byte hex from `openssl rand`; rotated periodically |
| Secrets | Pulled from env, never committed; use SSM/Vault |
| Network | Dashboard behind VPN / Tailscale; public surface minimal |

## 3. Troubleshooting & FAQ

**Dashboard returns 401** — set `BASELITHBOT_DASHBOARD_TOKEN` and present
it as `Authorization: Bearer <token>` or `?token=<token>`.

**`baselithbot_dashboard_open` warning in logs** — dev mode active
because no token is configured. Set one before exposing the server.

**`Computer Use is disabled`** — flip
`baselithbot.computer_use.enabled = true` in `configs/plugins.yaml`
*and* flip the specific `allow_*` capability you need.

**`capability 'shell' is not allowed`** — set `allow_shell: true` **and**
populate `allowed_shell_commands`. Allowlist is first-token or
space-prefix match, not substring.

**`filesystem path escapes root`** — target resolved to a path outside
`filesystem_root`. Fix the path. Plugin refuses to follow `..` or
symlinks that cross the root.

**`rate limit exceeded`** — client IP exhausted the bucket for that
route. Tune the limiter or back off.

**Chromium fails to launch** — run
`playwright install chromium --with-deps` on Linux; on macOS confirm the
Python process bundle has Accessibility + Screen Recording permission.

**Inbound 413** — body exceeded 1 MiB; chunk upstream or trim payload.

**`pyautogui.FailSafeException`** — mouse hit a screen corner (built-in
safety). Disable with `pyautogui.FAILSAFE = False` only on throwaway
VMs.

**React bundle 503** — `ui/dist` missing. Build it
(`cd plugins/baselithbot/ui && npm install && npm run build`) or ship
the package so `ui/dist/**/*` is included.

**Model update rejected (422)** — posted `{provider, model}` not in
`KNOWN_PROVIDERS` / `KNOWN_VISION_PROVIDERS`. Add to catalog if vetted.

**WebSocket pairing closes immediately (code 4290)** — rate limit
exceeded (20/min). Throttle handshake attempts.

**Vision LLM returns bad JSON** — investigate via structured logs with
`baselithbot_step` events; check VisionService prompt template and
failover chain (see [models.md](./models.md)).

**Docker sandbox unavailable** — doctor reports docker unreachable;
manager falls back to in-process execution. Either start the daemon or
ignore if in-process is acceptable.

**Tailscale status fails** — confirm the `tailscale` binary on `$PATH`
and the daemon is running. Set `TAILSCALE_AUTHKEY` for provisioning.

**macOS mouse moves but clicks don't register** — grant Accessibility
in *System Settings* **after** the Python process is already running;
macOS caches permission per-bundle-id and may require a full restart.

## 4. Runbook — incident response

1. **Identify** — correlate `baselithbot_tool_errors_total` spike with
   audit log timestamps.
2. **Contain** — flip `allow_shell: false` + `allow_filesystem: false`
   in `plugins.yaml`, reload config.
3. **Rotate** — new `BASELITHBOT_DASHBOARD_TOKEN`; revoke paired nodes
   (`DELETE /dash/nodes/{node_id}`); invalidate pairing tokens by
   restarting the process.
4. **Investigate** — grep audit log for denied actions; review structured
   logs around the event (`baselithbot_step` + `baselithbot_tool_error`).
5. **Report** — preserve audit log snapshot off-host.

## 5. Upgrade path

When bumping the plugin version:

1. Check `manifest.yaml` `python_dependencies` diff against the runtime
   environment.
2. If a new Computer Use capability flag is introduced, default it to
   `False` (opt-in) and document the flip.
3. Update `KNOWN_PROVIDERS` / `KNOWN_VISION_PROVIDERS` if LLM models
   change.
4. Re-run the dashboard build (`npm run build`) — bundle is pinned.
5. Update `MEMORY.md`-visible configs only if the change is operational.

## 6. Roadmap

- **V1.1** — diff-screenshot vision feedback loop (detect UI changes
  between steps; cut redundant re-planning).
- **V1.2** — multi-session manager: parallel BrowserContexts under one
  agent for concurrent runs.
- **V1.3** — DOM-LLM semantic selector synthesis (no more hand-written
  CSS selectors in MCP tool args).
- **V1.4** — per-session Docker sandboxing for Computer Use shell.
- **V2.x** — full dashboard RBAC (roles per endpoint, OIDC provider),
  cross-plugin Canvas A2UI handoff, remote Tailscale-only control plane,
  CSP header on UI bundle.
