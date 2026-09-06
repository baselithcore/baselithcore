# Configuration reference

The plugin reads its block from `configs/plugins.yaml`. Each group maps to a
Pydantic model; unknown keys raise at startup.

## Top-level keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `false` | Master toggle consumed by the plugin registry |
| `headless` | bool | `true` | Chromium `--headless=new` vs windowed |
| `max_steps` | int | `20` | Upper bound on the Observe→Plan→Act loop |
| `viewport_width` | int | `1280` | Playwright viewport |
| `viewport_height` | int | `720` | Playwright viewport |
| `stealth` | object | see below | Stealth countermeasures |
| `computer_use` | object | see below | OS-level controls (opt-in) |

## `stealth:` — `StealthConfig`

| Key | Default | Notes |
|---|---|---|
| `enabled` | `true` | Master toggle |
| `rotate_user_agent` | `true` | Pick a random UA from `user_agents` at start |
| `mask_webdriver` | `true` | `navigator.webdriver = undefined` |
| `spoof_languages` | `["en-US", "en"]` | `navigator.languages` + `Accept-Language` |
| `spoof_timezone` | `"UTC"` | `Intl.DateTimeFormat().resolvedOptions().timeZone` |
| `user_agents` | 3 built-in Chrome variants | Override to widen the pool |

Also perturbed when stealth is enabled (not independently toggleable):
`navigator.plugins`, `navigator.hardwareConcurrency`, WebGL
`UNMASKED_VENDOR_WEBGL`, and 2D canvas `ImageData` noise. See
[Stealth](../guide/stealth.md).

## `computer_use:` — `ComputerUseConfig`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | **Master switch** — until flipped, no Computer Use tool runs |
| `allow_mouse` | `true` | `mouse_move`, `mouse_click`, `mouse_scroll` |
| `allow_keyboard` | `true` | `kbd_type`, `kbd_press`, `kbd_hotkey` |
| `allow_screenshot` | `true` | `desktop_screenshot`, `screen_size` |
| `allow_shell` | `false` | `shell_run` (needs `allowed_shell_commands`) |
| `allow_filesystem` | `false` | `fs_read`, `fs_write`, `fs_list` (needs `filesystem_root`) |
| `allowed_shell_commands` | `[]` | First-token allowlist (exact OR space-prefix match) |
| `shell_timeout_seconds` | `30.0` | Hard timeout (1–600) per shell invocation |
| `filesystem_root` | `None` | Absolute path all fs ops are confined under |
| `filesystem_max_bytes` | `10_000_000` | Per-write byte cap |
| `audit_log_path` | `None` | JSON-Lines path; unset → structured logs only |
| `require_approval_for` | `[]` | Capabilities gated through the [Approvals](../guide/approvals.md) queue: `mouse`, `keyboard`, `screenshot`, `shell`, `filesystem` |
| `approval_timeout_seconds` | `120.0` | Seconds to wait for operator approval before auto-denying (1–3600) |

Full safety model: [Security & RBAC](security.md). Approval semantics:
[Approvals](../guide/approvals.md).

## Runtime configuration overlay

Dashboard writes to `computer_use` and `stealth` — from the
[Computer Use](../guide/computer-use.md) and [Stealth](../guide/stealth.md)
pages — persist through `RuntimeConfigStore` to
`plugins/baselithbot/.state/runtime_config.json` (atomic write,
`threading.Lock`). The overlay merges on top of the boot config whenever
`effective_computer_use_config()` / `effective_stealth_config()` are
evaluated, and every change invalidates the cached agent so the next run
rebuilds with the fresh guardrails.

| Route | Effect |
|---|---|
| `GET /dash/computer-use` | Read effective config (boot + overlay). |
| `PUT /dash/computer-use` | Validate + persist overlay, invalidate agent, emit SSE `computer_use.updated`. |
| `GET /dash/stealth` | Read effective Stealth config. |
| `PUT /dash/stealth` | Validate + persist + invalidate + SSE `stealth.updated`. |

Overlay files are excluded from git via `plugins/*/.state/` in
`.gitignore`.

## Environment variables

| Env var | Purpose |
|---|---|
| `BASELITHBOT_DASHBOARD_TOKEN` | Bearer token for dashboard write endpoints. Unset = open dev mode (one-shot warning). Generate: `openssl rand -hex 32`. |
| `BASELITHBOT_SECRET_KEY` | Fernet master key encrypting provider API keys at rest. Unset = auto-generated once under `<state>/.secret_key` (mode `0600`). |
| `BASELITHBOT_REPLAY_ENCRYPTION_KEY` | Optional Fernet key encrypting replay screenshots at rest. |
| `ELEVENLABS_API_KEY` | Optional; enables the ElevenLabs voice provider. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | LLM + Vision providers, resolved through `core.config.services`. |
| `TAILSCALE_AUTHKEY` | Optional gateway provisioning. |

API keys submitted through the [Models](../guide/models.md) dashboard page
are never echoed back plaintext — reads return a masked preview
(`***<last4>`) only; storage is encrypted (see [Security & RBAC](security.md)).

## Model preferences (persisted JSON)

Persisted to `plugins/baselithbot/.state/model_preferences.json` via
`ModelPreferenceStore`. Defaults: LLM provider `ollama`/`llama3.2`, vision
`openai`/`gpt-4o`, temperature `0.7`. Writes are bounded to a known-provider
catalog so an endpoint cannot smuggle an arbitrary string downstream. Full
reference: [Models](../guide/models.md).

## Example (full block)

```yaml
baselithbot:
  enabled: true
  headless: true
  max_steps: 25
  viewport_width: 1280
  viewport_height: 720
  stealth:
    enabled: true
    rotate_user_agent: true
    mask_webdriver: true
    spoof_languages: ["en-US", "en"]
    spoof_timezone: "UTC"
  computer_use:
    enabled: true
    allow_mouse: true
    allow_keyboard: true
    allow_screenshot: true
    allow_shell: true
    allow_filesystem: true
    allowed_shell_commands:
      - "ls"
      - "pwd"
      - "echo"
      - "git status"
      - "git log"
    shell_timeout_seconds: 30
    filesystem_root: "/var/lib/baselithbot/workspace"
    filesystem_max_bytes: 10000000
    audit_log_path: "/var/log/baselithbot/computer_use.jsonl"
    require_approval_for: ["shell", "filesystem"]
    approval_timeout_seconds: 120
```

## Onboarding wizard

```bash
baselith baselithbot onboard                  # prints YAML block
baselith baselithbot onboard --write          # writes to configs/plugins.yaml
baselith baselithbot onboard --install-daemon # install launchd/systemd unit
```

macOS installs to `~/Library/LaunchAgents` via `launchctl`; Linux installs a
user-scope unit under `~/.config/systemd/user` via `systemctl --user`. Pair
with `--dry-run` to preview target paths without mutating the system.

## Pairing / DM allowlist

```bash
baselith baselithbot pairing approve slack U12345ABC
baselith baselithbot pairing approve telegram 99887766
baselith baselithbot pairing list
baselith baselithbot pairing token
```

`approve` persists an entry under `baselithbot.dm_policy.<channel>.allowed_senders`
in `configs/plugins.yaml` (idempotent). See [Channels](../guide/channels.md)
for the inbound path this gates.
