# Computer Use safety model

[← Index](./README.md)

Implements the Anthropic Computer Use safety recipe end-to-end. Source of
truth: [`computer_use.py`](../computer_use.py),
[`shell_exec.py`](../shell_exec.py), [`filesystem.py`](../filesystem.py),
[`secret_redaction.py`](../secret_redaction.py).

## 1. Layered gates

1. **Master switch** — `computer_use.enabled = false` by default. Tools
   immediately return `{status: "denied"}` without touching the OS.
2. **Capability flags** — `allow_mouse`, `allow_keyboard`,
   `allow_screenshot`, `allow_shell`, `allow_filesystem` gate
   independently. `ComputerUseConfig.require_enabled("shell")` raises
   `ComputerUseError` when master OR per-capability flag is off.
3. **Shell allowlist** — first token (split with `shlex`) of every
   invocation must match `allowed_shell_commands` by exact-match OR
   space-prefix (`"git status"` allows `git status --short` but not
   `git push`). `shell=False` always — argv vector, never a string.
4. **Shell timeout** — hard kill at `shell_timeout_seconds` (default
   30s). stdout/stderr truncated to reasonable bytes.
5. **Filesystem scoping** — every path resolves via `Path.resolve()` and
   must `relative_to(filesystem_root)` — `..` traversal blocked. Per-write
   byte cap `filesystem_max_bytes`. Symlinks crossing the root rejected.
6. **Audit log** — JSON-Lines append to `audit_log_path` with batched
   flush. Sensitive keys (`token`, `password`, `secret`, `api_key`,
   `webhook_url`, …) redacted via [`secret_redaction.py`](../secret_redaction.py)
   both in the log file and the structured log line.
7. **Denied vs error** — capability denials return `denied`; runtime
   failures return `error`. Neither raises to the orchestrator.

## 2. `ComputerUseConfig` fields

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `false` | Master switch |
| `allow_mouse` | `true` | `pyautogui` mouse primitives |
| `allow_keyboard` | `true` | `pyautogui` keyboard primitives |
| `allow_screenshot` | `true` | `mss` screenshots |
| `allow_shell` | `false` | Subprocess execution |
| `allow_filesystem` | `false` | `ScopedFileSystem` r/w/list |
| `allowed_shell_commands` | `[]` | First-token allowlist |
| `shell_timeout_seconds` | `30.0` | 1.0–600.0 |
| `filesystem_root` | `None` | Absolute path confining fs ops |
| `filesystem_max_bytes` | `10_000_000` | Per-write byte cap |
| `audit_log_path` | `None` | JSON-Lines audit sink |

## 3. Audit log format

```jsonl
{"ts":1724512345.12,"action":"shell_run","cmd":["git","status"],"status":"success","duration_ms":42}
{"ts":1724512345.88,"action":"fs_write","path":"/var/lib/baselithbot/workspace/out.txt","bytes":128,"status":"success"}
{"ts":1724512346.01,"action":"shell_run","cmd":["rm","-rf","/"],"status":"denied","reason":"first-token not allowlisted"}
```

Fields:

- `ts` — Unix timestamp.
- `action` — one of `shell_run`, `fs_read`, `fs_write`, `fs_list`,
  `mouse_*`, `kbd_*`, `desktop_screenshot`.
- `status` — `success` / `denied` / `error`.
- Action-specific fields, post-redaction.

Batching: default 16 entries or 5s interval (whichever first). Flush on
process shutdown through `AuditLogger.close()`.

## 4. Shell allowlist recipes

| Goal | Allowlist entry | Example accepted | Example rejected |
|------|-----------------|------------------|------------------|
| Read repo status only | `git status` | `git status --short` | `git push origin main` |
| Ls anywhere | `ls` | `ls -la /tmp` | `ls; rm -rf /` (not argv-split) |
| Python script runner | `/usr/bin/python3 /opt/scripts/` | `/usr/bin/python3 /opt/scripts/run.py` | `/usr/bin/python3 /etc/shadow` |

Shell metacharacters never interpreted — invocation uses argv vector
`subprocess.run(cmd, shell=False, …)`.

## 5. Filesystem scoping

[`ScopedFileSystem`](../filesystem.py) resolves each target path:

```python
root = Path(config.filesystem_root).resolve()
target = (root / user_path).resolve()
target.relative_to(root)   # ValueError if outside
```

Additional rules:

- Writes enforce `filesystem_max_bytes`.
- Symbolic links traversing outside root raise.
- Parent directories created lazily only inside `root`.
- Read returns UTF-8 by default; binary mode via explicit flag.

## 6. Recommended deployment posture

- Run under a dedicated unix user with a scoped `$HOME`.
- Set `filesystem_root` to a disposable directory
  (e.g. `/var/lib/baselithbot/workspace`).
- Keep `allowed_shell_commands` minimal.
- Pipe `audit_log_path` to a WORM/append-only volume.
- On Linux, run under `Xvfb` in a VM/container — do **not** grant mouse
  control to the workstation that holds your keys.
- Export `BASELITHBOT_DASHBOARD_TOKEN`; never expose the dashboard on a
  shared network in dev mode.
- Monitor `baselithbot_tool_errors_total{tool="shell_run"}` for spikes
  indicating attacker probing the allowlist.

## 7. Incident response

If the audit log shows unexpected denials:

1. Grep for the offending first token: may indicate a prompt-injection
   attempt against the LLM.
2. Review structured logs around the same timestamp for `goal` content.
3. Rotate `BASELITHBOT_DASHBOARD_TOKEN` and revoke paired nodes via
   `DELETE /dash/nodes/{node_id}`.
4. Disable `allow_shell` / `allow_filesystem` until triage completes.
