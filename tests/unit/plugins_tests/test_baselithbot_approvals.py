"""Tests for ApprovalGate + /dash/approvals routes + gated tool integration."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.baselithbot.control.approvals import ApprovalGate, ApprovalStatus
from plugins.baselithbot.computer_use.config import (
    AuditLogger,
    ComputerUseConfig,
    ComputerUseError,
)
from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem
from plugins.baselithbot.plugin import BaselithbotPlugin
from plugins.baselithbot.api.router import create_router
from plugins.baselithbot.computer_use.shell_exec import ShellExecutor


def _build_app() -> tuple[FastAPI, BaselithbotPlugin, str]:
    state_dir = tempfile.mkdtemp(prefix="baselithbot-approvals-tests-")
    plugin = BaselithbotPlugin(state_dir=state_dir)
    app = FastAPI()
    app.include_router(create_router(plugin), prefix="/baselithbot")
    return app, plugin, state_dir


class TestApprovalGateUnit:
    @pytest.mark.asyncio
    async def test_approve_resolves_pending_request(self) -> None:
        gate = ApprovalGate()

        async def submitter() -> ApprovalStatus:
            req = await gate.submit(
                capability="shell",
                action="shell_run",
                params={"argv": ["ls"]},
                timeout_seconds=2.0,
            )
            return req.status

        async def approver() -> None:
            await asyncio.sleep(0.05)
            snap = await gate.snapshot()
            rid = snap["pending"][0]["id"]
            assert await gate.approve(rid, reason="ok")

        statuses = await asyncio.gather(submitter(), approver())
        assert statuses[0] == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_deny_resolves_pending_request(self) -> None:
        gate = ApprovalGate()

        async def submitter() -> ApprovalStatus:
            req = await gate.submit(
                capability="filesystem",
                action="fs_write",
                params={},
                timeout_seconds=2.0,
            )
            return req.status

        async def denier() -> None:
            await asyncio.sleep(0.05)
            snap = await gate.snapshot()
            rid = snap["pending"][0]["id"]
            assert await gate.deny(rid, reason="nope")

        statuses = await asyncio.gather(submitter(), denier())
        assert statuses[0] == ApprovalStatus.DENIED

    @pytest.mark.asyncio
    async def test_timeout_returns_timed_out(self) -> None:
        gate = ApprovalGate()
        req = await gate.submit(
            capability="mouse",
            action="mouse_click",
            params={"x": 1, "y": 1},
            timeout_seconds=0.1,
        )
        assert req.status == ApprovalStatus.TIMED_OUT


class TestApprovalRoutes:
    def test_list_empty(self) -> None:
        app, _, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/approvals")
        assert res.status_code == 200
        body = res.json()
        assert body["pending"] == []
        assert body["history"] == []
        assert body["totals"] == {
            "pending": 0,
            "history": 0,
            "approved": 0,
            "denied": 0,
            "timed_out": 0,
        }
        assert body["status_counts"] == {}
        assert body["capability_counts"] == {}
        assert body["action_counts"] == {}
        assert body["oldest_pending_ts"] is None
        assert body["next_expiry_ts"] is None
        assert body["latest_resolved_ts"] is None
        assert body["policy"] == {
            "enabled": False,
            "approval_timeout_seconds": 120.0,
            "enabled_capabilities": [],
            "gated_capabilities": [],
            "bypassed_capabilities": [],
        }

    def test_approve_unknown_returns_404(self) -> None:
        app, _, _ = _build_app()
        client = TestClient(app)
        res = client.post(
            "/baselithbot/dash/approvals/nonexistent/approve",
            json={"reason": None},
        )
        assert res.status_code == 404

    def test_end_to_end_approve_flow(self) -> None:
        app, plugin, _ = _build_app()
        client = TestClient(app)

        async def submitter() -> ApprovalStatus:
            req = await plugin.approvals.submit(
                capability="shell",
                action="shell_run",
                params={"argv": ["ls"]},
                timeout_seconds=3.0,
            )
            return req.status

        async def driver() -> ApprovalStatus:
            task = asyncio.create_task(submitter())
            # Let it register as pending.
            await asyncio.sleep(0.1)
            res = client.get("/baselithbot/dash/approvals")
            assert res.status_code == 200
            pending = res.json()["pending"]
            assert len(pending) == 1
            rid = pending[0]["id"]

            approve = client.post(
                f"/baselithbot/dash/approvals/{rid}/approve",
                json={"reason": "ok"},
            )
            assert approve.status_code == 200
            return await task

        status = asyncio.run(driver())
        assert status == ApprovalStatus.APPROVED

    def test_list_includes_policy_and_rollups(self) -> None:
        app, plugin, _ = _build_app()
        plugin.runtime_config.set_computer_use(
            ComputerUseConfig(
                enabled=True,
                allow_shell=True,
                allow_filesystem=True,
                require_approval_for=["shell"],
                approval_timeout_seconds=45.0,
            )
        )
        client = TestClient(app)

        async def submitter() -> ApprovalStatus:
            req = await plugin.approvals.submit(
                capability="shell",
                action="shell_run",
                params={"argv": ["echo", "hello"]},
                timeout_seconds=3.0,
            )
            return req.status

        async def driver() -> dict[str, object]:
            task = asyncio.create_task(submitter())
            await asyncio.sleep(0.1)
            pending_res = client.get("/baselithbot/dash/approvals")
            assert pending_res.status_code == 200
            pending_body = pending_res.json()
            assert pending_body["totals"]["pending"] == 1
            assert pending_body["policy"]["enabled"] is True
            assert pending_body["policy"]["gated_capabilities"] == ["shell"]
            assert pending_body["policy"]["enabled_capabilities"] == [
                "mouse",
                "keyboard",
                "screenshot",
                "shell",
                "filesystem",
            ]
            assert pending_body["policy"]["bypassed_capabilities"] == [
                "mouse",
                "keyboard",
                "screenshot",
                "filesystem",
            ]
            assert pending_body["capability_counts"] == {"shell": 1}
            assert pending_body["action_counts"] == {"shell_run": 1}
            rid = pending_body["pending"][0]["id"]

            deny = client.post(
                f"/baselithbot/dash/approvals/{rid}/deny",
                json={"reason": "not allowed"},
            )
            assert deny.status_code == 200
            assert await task == ApprovalStatus.DENIED

            final_res = client.get("/baselithbot/dash/approvals")
            assert final_res.status_code == 200
            return final_res.json()

        body = asyncio.run(driver())
        assert body["totals"]["pending"] == 0
        assert body["totals"]["history"] == 1
        assert body["totals"]["denied"] == 1
        assert body["status_counts"]["denied"] == 1
        assert body["latest_resolved_ts"] is not None


class TestGatedToolIntegration:
    @pytest.mark.asyncio
    async def test_filesystem_write_denied_raises_error(self) -> None:
        state_dir = tempfile.mkdtemp()
        audit_path = Path(state_dir) / "audit.jsonl"
        config = ComputerUseConfig(
            enabled=True,
            allow_filesystem=True,
            filesystem_root=state_dir,
            audit_log_path=str(audit_path),
            require_approval_for=["filesystem"],
            approval_timeout_seconds=2.0,
        )
        audit = AuditLogger(str(audit_path))
        gate = ApprovalGate()
        fs = ScopedFileSystem(config, audit, approvals=gate)

        async def denier() -> None:
            await asyncio.sleep(0.05)
            pending = await gate.pending()
            assert pending
            await gate.deny(pending[0].id, reason="blocked")

        with pytest.raises(ComputerUseError, match="operator denied"):
            await asyncio.gather(fs.write("x.txt", "hi"), denier())

    @pytest.mark.asyncio
    async def test_shell_gated_approved_runs(self) -> None:
        config = ComputerUseConfig(
            enabled=True,
            allow_shell=True,
            allowed_shell_commands=["echo"],
            shell_timeout_seconds=5.0,
            require_approval_for=["shell"],
            approval_timeout_seconds=2.0,
        )
        audit = AuditLogger(None)
        gate = ApprovalGate()
        shell = ShellExecutor(config, audit, approvals=gate)

        async def approver() -> None:
            await asyncio.sleep(0.05)
            pending = await gate.pending()
            assert pending
            await gate.approve(pending[0].id)

        results = await asyncio.gather(shell.run("echo hello"), approver())
        assert results[0]["return_code"] == 0
