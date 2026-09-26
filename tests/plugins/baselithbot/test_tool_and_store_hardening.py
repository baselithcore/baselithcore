"""Security/performance regressions: MCP tools, audit trail, stores, scheduler.

Each test pins one defect found in the 2026-09 baselithbot audit:

* ``tailscale_up/down/logout`` ran with Computer Use disabled;
* ``process_kill`` forwarded pid <= 0 (process-group / every-process
  targets) and the server's own pid to ``os.kill``;
* ``workspace_remove`` let the agent delete the primary workspace;
* per-request tool builders dropped buffered audit entries;
* channel ``webhook_url`` credentials came back in plaintext, and a masked
  value posted back replaced the stored secret;
* the rate limiter never evicted idle buckets;
* session overflow evicted the primary session;
* one slow cron job stalled every other job;
* an SSH host starting with ``-`` was parsed by ssh as an option.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from plugins.baselithbot.computer_use.config import ComputerUseConfig


def _tool(defs: list[dict[str, Any]], name: str) -> Any:
    return next(d["handler"] for d in defs if d["name"] == name)


# ---------------------------------------------------------------- tailscale


@pytest.mark.asyncio
async def test_tailscale_tools_denied_when_computer_use_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugins.baselithbot.computer_use import extra_tools
    from plugins.baselithbot.gateway import TailscaleProvisioner

    calls: list[str] = []

    async def _fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append("ran")
        return {"status": "success"}

    for name in ("up", "down", "logout"):
        monkeypatch.setattr(TailscaleProvisioner, name, staticmethod(_fake))
    defs = extra_tools.build_extra_tool_definitions(config=ComputerUseConfig())
    for tool in ("tailscale_up", "tailscale_down", "tailscale_logout"):
        result = await _tool(defs, f"baselithbot_{tool}")()
        assert result["status"] == "denied", tool
    assert calls == []

    enabled = ComputerUseConfig(enabled=True, allow_shell=True)
    defs = extra_tools.build_extra_tool_definitions(config=enabled)
    assert (await _tool(defs, "baselithbot_tailscale_down")())["status"] == "success"
    assert calls == ["ran"]


# ---------------------------------------------------------------- process kill


@pytest.mark.asyncio
@pytest.mark.parametrize("pid", [0, -1, -42, 1])
async def test_process_kill_refuses_group_and_init_targets(
    monkeypatch: pytest.MonkeyPatch, pid: int
) -> None:
    from plugins.baselithbot.computer_use.extra_tools import (
        build_extra_tool_definitions,
    )

    killed: list[int] = []
    monkeypatch.setattr(os, "kill", lambda p, _s: killed.append(p))
    cfg = ComputerUseConfig(enabled=True, allow_shell=True)
    kill = _tool(build_extra_tool_definitions(config=cfg), "baselithbot_process_kill")
    result = await kill(pid=pid)
    assert result["status"] == "denied"
    assert killed == []


@pytest.mark.asyncio
async def test_process_kill_refuses_own_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    from plugins.baselithbot.computer_use.extra_tools import (
        build_extra_tool_definitions,
    )

    killed: list[int] = []
    monkeypatch.setattr(os, "kill", lambda p, _s: killed.append(p))
    cfg = ComputerUseConfig(enabled=True, allow_shell=True)
    kill = _tool(build_extra_tool_definitions(config=cfg), "baselithbot_process_kill")
    assert (await kill(pid=os.getpid()))["status"] == "denied"
    assert (await kill(pid=424242))["status"] == "success"
    assert killed == [424242]


# ---------------------------------------------------------------- workspaces


@pytest.mark.asyncio
async def test_workspace_remove_tool_protects_primary(tmp_path: Path) -> None:
    from plugins.baselithbot.computer_use.extra_tools import (
        build_extra_tool_definitions,
    )
    from plugins.baselithbot.workspace import WorkspaceConfig, WorkspaceManager

    mgr = WorkspaceManager()
    mgr.create(WorkspaceConfig(name="main", primary=True))
    mgr.create(WorkspaceConfig(name="scratch"))
    remove = _tool(
        build_extra_tool_definitions(config=ComputerUseConfig(), workspaces=mgr),
        "baselithbot_workspace_remove",
    )
    assert (await remove(name="main"))["status"] == "denied"
    assert (await remove(name="scratch"))["status"] == "success"
    assert [w.config.name for w in mgr.list()] == ["main"]


# ---------------------------------------------------------------- audit trail


@pytest.mark.asyncio
async def test_rebuilt_tool_maps_do_not_lose_audit_entries(tmp_path: Path) -> None:
    from plugins.baselithbot.computer_use.config import flush_audit_loggers
    from plugins.baselithbot.computer_use.tools import build_computer_tool_definitions

    audit_path = tmp_path / "audit.jsonl"
    root = tmp_path / "root"
    root.mkdir()
    cfg = ComputerUseConfig(
        enabled=True,
        allow_filesystem=True,
        filesystem_root=str(root),
        audit_log_path=str(audit_path),
    )
    # The dashboard rebuilds the tool map for every invocation.
    for i in range(3):
        defs = build_computer_tool_definitions(cfg)
        result = await _tool(defs, "baselithbot_fs_write")(
            path=f"f{i}.txt", content="x"
        )
        assert result["status"] == "success"
    flush_audit_loggers()
    actions = [
        json.loads(line)["action"] for line in audit_path.read_text().splitlines()
    ]
    assert actions.count("fs_write") == 3


# ---------------------------------------------------------------- channel config


def test_channel_webhook_url_is_masked_and_mask_echo_is_ignored(state_dir: str) -> None:
    from plugins.baselithbot.channels.config_store import (
        ChannelConfigStore,
        merge_config_update,
    )

    secret_url = "https://hooks.slack.com/services/T000/B000/XXXXsecretXXXX"
    store = ChannelConfigStore(state_dir=state_dir)
    store.set("slack", {"webhook_url": secret_url, "default_channel": "#ops"})
    snap = store.snapshot_entry("slack", ("webhook_url",))
    assert snap["safe_config"]["webhook_url"] != secret_url
    assert secret_url not in json.dumps(snap)

    stored = store.get_config("slack") or {}
    echoed = {
        "webhook_url": snap["safe_config"]["webhook_url"],
        "default_channel": "#dev",
    }
    merged = merge_config_update(stored, echoed, [])
    assert merged["webhook_url"] == secret_url  # the mask did not overwrite it
    assert merged["default_channel"] == "#dev"
    assert merge_config_update(stored, {"webhook_url": "https://new"}, [])[
        "webhook_url"
    ] == ("https://new")
    mode = (Path(state_dir) / "channel_configs.enc.json").stat().st_mode & 0o777
    assert mode == 0o600


# ---------------------------------------------------------------- rate limiter


def test_rate_limiter_evicts_idle_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    from plugins.baselithbot.policies import RateLimiter

    monkeypatch.setattr(RateLimiter, "SWEEP_THRESHOLD", 8)
    limiter = RateLimiter(window_seconds=1.0, max_events=1)
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    for i in range(8):
        assert limiter.consume(f"ip-{i}")
    now[0] += 5.0  # every bucket is now idle
    assert limiter.consume("fresh")
    assert len(limiter.status()) == 1
    assert not limiter.consume("fresh")  # limiting itself is unchanged


# ---------------------------------------------------------------- sessions


def test_session_overflow_keeps_primary() -> None:
    from plugins.baselithbot.sessions import SessionManager

    mgr = SessionManager(max_sessions=3)
    primary = mgr.create(title="operator", primary=True)
    for i in range(10):
        mgr.create(title=f"ch:slack:user{i}")
    ids = [s.id for s in mgr.list()]
    assert primary.id in ids
    assert len(ids) == 3


# ---------------------------------------------------------------- cron


@pytest.mark.asyncio
async def test_slow_cron_job_does_not_stall_others() -> None:
    from plugins.baselithbot.cron.scheduler import CronScheduler

    sched = CronScheduler()
    release = asyncio.Event()
    fast_runs = 0

    async def slow() -> None:
        await release.wait()

    async def fast() -> None:
        nonlocal fast_runs
        fast_runs += 1

    sched.add_interval("slow", slow, seconds=1.0)
    sched.add_interval("fast", fast, seconds=1.0)
    sched.trigger("slow")
    sched.trigger("fast")
    await sched.start()
    try:
        for _ in range(100):
            if fast_runs:
                break
            await asyncio.sleep(0.02)
        assert fast_runs >= 1, "fast job blocked behind the slow one"
        assert sched.get("slow")["runs"] == 0  # still running, not duplicated
    finally:
        release.set()
        await sched.stop()


# ---------------------------------------------------------------- ssh


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "-oProxyCommand=touch /tmp/pwn"),
        ("user", "-oProxyCommand=x"),
        ("host", "a b"),
    ],
)
def test_ssh_gateway_rejects_option_shaped_targets(field: str, value: str) -> None:
    from pydantic import ValidationError

    from plugins.baselithbot.gateway import SSHGatewayConfig

    kwargs: dict[str, Any] = {"host": "example.org", "allowed_commands": ["ls"]}
    kwargs[field] = value
    with pytest.raises(ValidationError):
        SSHGatewayConfig(**kwargs)
