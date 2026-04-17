# Configuration reference

[← Index](./README.md)

The plugin reads its block from [`configs/plugins.yaml`](../../../configs/plugins.yaml).
Each group maps to a Pydantic model; unknown keys raise at startup.

## 1. Top-level keys

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `enabled` | bool | `false` | Master toggle consumed by plugin registry |
| `headless` | bool | `true` | Chromium `--headless=new` vs windowed |
| `max_steps` | int | `20` | Upper bound on Observe→Plan→Act loop |
| `viewport_width` | int | `1280` | Playwright viewport |
| `viewport_height` | int | `720` | Playwright viewport |
| `stealth` | object | see §2 | Stealth countermeasures |
| `computer_use` | object | see §3 | OS-level controls (opt-in) |

## 2. `stealth:` — `StealthConfig`

| Key | Default | Notes |
|-----|---------|-------|
| `enabled` | `true` | Master toggle |
| `rotate_user_agent` | `true` | Pick random UA from `user_agents` at start |
| `mask_webdriver` | `true` | `navigator.webdriver = undefined` |
| `spoof_languages` | `["en-US", "en"]` | `navigator.languages` + Accept-Language |
| `spoof_timezone` | `"UTC"` | `Intl.DateTimeFormat().resolvedOptions().timeZone` |
| `user_agents` | 3 built-in Chrome variants | Override to widen pool |

Perturbations also hit `navigator.plugins`, `navigator.hardwareConcurrency`,
WebGL `UNMASKED_VENDOR_WEBGL`, 2D canvas ImageData noise. See
[`stealth.py`](../stealth.py).

## 3. `computer_use:` — `ComputerUseConfig`

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `false` | **Master switch** — until flipped, *no* Computer Use tool runs |
| `allow_mouse` | `true` | Enables `mouse_move`, `mouse_click`, `mouse_scroll` |
| `allow_keyboard` | `true` | Enables `kbd_type`, `kbd_press`, `kbd_hotkey` |
| `allow_screenshot` | `true` | Enables `desktop_screenshot`, `screen_size` |
| `allow_shell` | `false` | Enables `shell_run` (needs `allowed_shell_commands`) |
| `allow_filesystem` | `false` | Enables `fs_read`, `fs_write`, `fs_list` (needs `filesystem_root`) |
| `allowed_shell_commands` | `[]` | First-token allowlist (exact OR space-prefix match) |
| `shell_timeout_seconds` | `30.0` | Hard timeout (1–600) per shell invocation |
| `filesystem_root` | `None` | Absolute path under which all fs ops confined |
| `filesystem_max_bytes` | `10_000_000` | Per-write byte cap |
| `audit_log_path` | `None` | JSON-Lines path; unset → only structured logs |

Safety model detail: [computer-use.md](./computer-use.md).

## 4. Environment variables

| Env var | Purpose |
|---------|---------|
| `BASELITHBOT_DASHBOARD_TOKEN` | Bearer token for dashboard write endpoints. Unset = open dev mode (single warning logged). |
| `ELEVENLABS_API_KEY` | Optional; enables ElevenLabs voice provider |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | LLM + Vision providers (read from `core.config.services`) |
| `TAILSCALE_AUTHKEY` | Gateway provisioning (optional) |

API keys are **never** echoed back by `/dash/models`; they stay in env
vars under `core.config.services`.

## 5. Model preferences (persisted JSON)

Persisted to
`plugins/baselithbot/.state/model_preferences.json` via
[`ModelPreferenceStore`](../model_config.py). Defaults: provider
`ollama/llama3.2`, vision `openai/gpt-4o`, temperature `0.7`.

Known provider catalog (`KNOWN_PROVIDERS`, `KNOWN_VISION_PROVIDERS`)
bounds every write so endpoint cannot smuggle arbitrary strings
downstream.

Full reference: [models.md](./models.md).

## 6. Example (full block)

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
```

## 7. Onboarding wizard

```bash
baselith baselithbot onboard              # prints YAML block
baselith baselithbot onboard --write      # writes to configs/plugins.yaml
```
