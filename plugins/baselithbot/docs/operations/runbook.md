# Runbook & troubleshooting

## Channel setup

1. Pick an adapter from the 24 available (see [Channels](../guide/channels.md)
   for the full list).
2. Configure it from the dashboard: **Channels → select adapter → edit
   config** (webhook URL, tokens, etc.), or write directly via
   `PUT /dash/channels/{name}/config` (🔒).
3. **Start** the adapter (`POST /dash/channels/{name}/start`, 🔒) and
   **test** it (`POST /dash/channels/{name}/test`, 🔒).
4. Point the provider's webhook at `POST /baselithbot/inbound/{channel}` —
   this route is intentionally unauthenticated (providers can't present a
   bearer token) but is body-capped at 1 MiB and passed through
   `DMPairingPolicy`.
5. For DM-capable channels, approve senders before they can reach the
   agent in a DM:

   ```bash
   baselith baselithbot pairing approve slack U12345ABC
   baselith baselithbot pairing approve telegram 99887766
   baselith baselithbot pairing list     # dump the current allowlist
   ```

   An unpaired DM sender gets `{"status": "denied", "reason": ...}` instead
   of reaching a handler.

## MCP setup

`get_mcp_tools()` is merged into the framework MCP server automatically at
plugin registration — no separate configuration step. All 37+ tools
(browser, Computer Use, OpenClaw parity, extras, Set-of-Mark — full catalog
in [API reference](../reference/api.md)) become available to any MCP client
connected through `core.mcp.server`. To scope what an MCP client can
actually *do*, configure the underlying capability, not the tool list:

- Browser tools are always available (guarded by the sanitizer, not a
  flag).
- Computer Use tools return `{"status": "denied"}` until the corresponding
  `computer_use.allow_*` flag is on — see
  [Computer Use](../guide/computer-use.md).
- Shell/filesystem tools additionally need `allowed_shell_commands` /
  `filesystem_root` populated.

## Cron setup

Built-in jobs (e.g. `replay.prune_history`, 14-day retention) register at
startup via `_bootstrap.register_default_cron_jobs`. To add an
operator-defined job:

- From the dashboard: [Cron](../guide/cron.md) → **create** from the job
  catalog, set the crontab expression and interval.
- Programmatically: `POST /dash/crons` (🔒) with a name from
  `GET /dash/crons/catalog`.

Custom jobs persist to `plugins/baselithbot/.state/custom_crons.json` and
are restored on the next boot.

## Voice setup

- **System TTS** (default, offline) — `say` on macOS, `espeak` on Linux; no
  configuration needed.
- **ElevenLabs** (opt-in) — set `ELEVENLABS_API_KEY`; the voice provider
  selector switches automatically once the key is present.
- Invoke via the `baselithbot_voice_tts` MCP tool, or from a channel/session
  flow that calls into the voice subsystem.

## Deployment recipes

### Docker (headless)

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

### Behind nginx (WebSocket + SSE)

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

FastAPI must trust the proxy headers for the rate limiter to key on the
real client IP:

```python
from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
```

### Systemd (VM target for Computer Use)

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

Or use the bundled installer: `baselith baselithbot onboard
--install-daemon` (macOS → `launchd`, Linux → `systemd --user`); pair with
`--dry-run` to preview paths first.

### Environment hardening

| Aspect | Recommendation |
|---|---|
| OS user | Dedicated, scoped `$HOME`, no sudo |
| Filesystem root | Disposable directory on an ephemeral volume |
| Audit log | Append-only / WORM volume |
| Dashboard token | 32-byte hex, rotated periodically |
| Secrets | Pulled from env, never committed |
| Network | Dashboard behind VPN / Tailscale; minimal public surface |

## Prometheus scrape + alerts

```yaml
scrape_configs:
  - job_name: baselithbot
    metrics_path: /baselithbot/metrics
    static_configs:
      - targets: ["localhost:8000"]
```

```yaml
- alert: BaselithbotShellErrorsSpiking
  expr: rate(baselithbot_tool_errors_total{tool="shell_run"}[5m]) > 0.1
  for: 10m
  annotations:
    summary: "Shell tool errors elevated — review audit log"

- alert: BaselithbotRunFailureRate
  expr: |
    sum(rate(baselithbot_run_total{result!="success"}[15m]))
      / sum(rate(baselithbot_run_total[15m])) > 0.5
  for: 15m
  annotations:
    summary: "Run failure rate > 50% for 15m"
```

## Troubleshooting & FAQ

**Dashboard returns 401** — set `BASELITHBOT_DASHBOARD_TOKEN` and present it
as `Authorization: Bearer <token>` or `?token=<token>`.

**`baselithbot_dashboard_open` warning in logs** — dev mode is active
because no token is configured. Set one before exposing the server.

**"Computer Use is disabled"** — flip `baselithbot.computer_use.enabled:
true` *and* the specific `allow_*` capability you need — see
[Computer Use](../guide/computer-use.md).

**"capability 'shell' is not allowed"** — set `allow_shell: true` **and**
populate `allowed_shell_commands`. The allowlist matches first-token or
space-prefix, not substring.

**"filesystem path escapes root"** — the target resolved outside
`filesystem_root`; the plugin refuses `..` traversal and cross-root
symlinks by design.

**"rate limit exceeded"** — the client IP exhausted the bucket for that
route (see [Security & RBAC](../reference/security.md) for the table);
tune the limiter or back off.

**Chromium fails to launch** — run `playwright install chromium
--with-deps` on Linux; on macOS confirm the Python process has
Accessibility + Screen Recording permission.

**Inbound `413`** — body exceeded 1 MiB; chunk upstream or trim the
payload.

**`pyautogui.FailSafeException`** — the mouse hit a screen corner (built-in
safety). Disable with `pyautogui.FAILSAFE = False` only on throwaway VMs.

**React bundle returns 503 with a build-instructions page** — `ui/dist` is
missing. Build it: `cd plugins/baselithbot/ui && npm install && npm run
build`, then restart the backend — the mount only happens once, at app
construction.

**Model update rejected (422)** — posted `{provider, model}` isn't in the
known-provider catalog. See [Models](../guide/models.md).

**WebSocket pairing closes immediately (code 4290)** — handshake rate limit
exceeded (20/min); throttle attempts.

**Docker sandbox unavailable** — [Doctor](../guide/doctor.md) reports Docker
unreachable; the session manager falls back to in-process execution.
Either start the daemon or accept the degradation.

**Tailscale status fails** — confirm the `tailscale` binary is on `$PATH`
and the daemon is running; set `TAILSCALE_AUTHKEY` for provisioning.

**macOS mouse moves but clicks don't register** — grant Accessibility
*after* the Python process is already running; macOS caches permission
per-bundle-id and may require a full process restart.

## Incident response

1. **Identify** — correlate a `baselithbot_tool_errors_total` spike with
   [audit log](../guide/audit-log.md) timestamps.
2. **Contain** — flip `allow_shell: false` and `allow_filesystem: false` in
   `configs/plugins.yaml`, reload.
3. **Rotate** — issue a new `BASELITHBOT_DASHBOARD_TOKEN`; revoke paired
   nodes (`DELETE /dash/nodes/{node_id}`); a process restart invalidates
   any outstanding pairing tokens.
4. **Investigate** — grep the audit log for denied actions; cross-reference
   structured logs (`baselithbot_step`, `baselithbot_tool_error`) around
   the same timestamps.
5. **Report** — preserve an audit log snapshot off-host.

## Testing

```bash
python -m pytest tests/unit/plugins_tests/ -k baselithbot
python -m pytest tests/unit/plugins_tests/ -k baselithbot -m "not slow"
python -m pytest --cov=plugins/baselithbot --cov-report=html

python scripts/check_official_plugin_typing.py
python scripts/check_architecture_boundaries.py
```
