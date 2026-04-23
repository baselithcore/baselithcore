# Human-in-the-loop approvals

[← Index](./README.md)

Source of truth: [`approvals.py`](../approvals.py).
Dashboard surface: [`dashboard/routes/approvals.py`](../dashboard/routes/approvals.py),
UI page [`ui/src/pages/Approvals.tsx`](../ui/src/pages/Approvals.tsx).

## 1. Why

Privileged Computer Use actions (`shell_run`, `fs_write`, `mouse_click`,
`kbd_type`, …) can move fast in an autonomous loop. The approval gate
interposes a bounded pause: the agent submits the action, the dashboard
shows it with full parameters + an expiry countdown, and the operator
decides whether the runtime proceeds or short-circuits with a
`ComputerUseError`.

This is the pattern used by Cursor Agent Mode / Claude Computer Use for
destructive steps, bolted onto every Baselithbot capability.

## 2. Lifecycle

```text
  pending ──approve──▶ approved   (action runs)
     │                              │
     ├──deny────────▶ denied        ▼  ComputerUseError raised,
     │                              │  action skipped, audit logged
     └──timeout────▶ timed_out ◄────┘
```

## 3. `ComputerUseConfig` fields

| Field | Default | Meaning |
|-------|---------|---------|
| `require_approval_for` | `[]` | Capabilities gated: any subset of `mouse`, `keyboard`, `screenshot`, `shell`, `filesystem`. Empty list disables the gate entirely. |
| `approval_timeout_seconds` | `120.0` | Seconds a request stays pending before auto-denial. Clamp: 1 – 3600. |

Both fields mutate live via `PUT /dash/computer-use` and invalidate the
cached agent so the next run rebuilds with the new policy.

## 4. REST routes (`/baselithbot/dash/*`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/approvals` | 🔓 | Pending + last-50 history (`totals.pending`, `totals.history`) |
| POST | `/dash/approvals/{id}/approve` | 🔒 5/min | Resolve as `approved` (`{reason}` optional, ≤500 chars) |
| POST | `/dash/approvals/{id}/deny` | 🔒 5/min | Resolve as `denied` |

All four state transitions broadcast on the dashboard event bus:
`approval.pending`, `approval.resolved`, `approval.approved`,
`approval.denied`.

## 5. Request envelope

```json
{
  "id": "9672d34596944d20aacbdc9d51224e7b",
  "capability": "shell",
  "action": "shell_run",
  "params": {"argv": ["git", "status"], "cwd": null},
  "submitted_at": 1724512345.12,
  "timeout_seconds": 120.0,
  "status": "pending",
  "resolved_at": null,
  "reason": null,
  "expires_at": 1724512465.12
}
```

## 6. Callable Python API

```python
from plugins.baselithbot.approvals import ApprovalGate, ApprovalStatus

gate = ApprovalGate()
req = await gate.submit(
    capability="filesystem",
    action="fs_write",
    params={"path": "/var/lib/work/out.txt", "bytes": 512},
    timeout_seconds=60.0,
)
assert req.status in (
    ApprovalStatus.APPROVED, ApprovalStatus.DENIED, ApprovalStatus.TIMED_OUT
)
```

`ApprovalGate` is process-local, asyncio-native, and listens on a
broadcast queue (`subscribe()` / `unsubscribe()`) for dashboards.

## 7. Audit trail

Every resolution is logged through the existing
[`AuditLogger`](../computer_use.py) with one of:

- `shell_run.denied` / `shell_run.timed_out`
- `fs_write.denied` / `fs_write.timed_out`
- `mouse_click.denied`, `kbd_type.denied`, …

Reason strings and `approval_id` are included. Approved actions flow
through the normal audit path (`shell_run`, `fs_write`, `mouse_click`).

## 8. Dashboard UI

The **Approvals** page polls `/dash/approvals` every second. The
pending table shows:

- Submitted timestamp (relative)
- Action + capability
- JSON-serialized parameters
- Countdown pill (ambra <30s, rosso <10s)
- Reason input + Approve / Deny buttons

A history panel displays the last 50 resolutions with status badges.

## 9. Testing

Coverage: [`tests/unit/plugins_tests/test_baselithbot_approvals.py`](../../../tests/unit/plugins_tests/test_baselithbot_approvals.py)
— 8 tests asserting gate unit semantics (approve / deny / timeout), route
behaviour, and integration into `ScopedFileSystem` + `ShellExecutor`.
