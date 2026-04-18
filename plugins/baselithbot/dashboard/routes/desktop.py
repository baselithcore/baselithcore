"""Desktop (Computer Use) tool invocation routes.

Exposes the Computer Use MCP tool surface as direct HTTP endpoints so the
dashboard can drive single-tool actions (screenshot, shell, mouse, kbd,
fs) without going through the full MCP client. Every call re-resolves the
current ``ComputerUseConfig`` so runtime overlay edits take effect
immediately after save.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...policies import RateLimiter
from ...usage import UsageEvent
from ..bus import _BUS
from ..security import enforce

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


class DesktopToolInvokeRequest(BaseModel):
    """Generic tool invocation payload — arguments forwarded to the handler."""

    args: dict[str, Any] = Field(default_factory=dict)


class DesktopTaskRequest(BaseModel):
    """Natural-language desktop task dispatch payload."""

    goal: str = Field(..., min_length=1, max_length=2000)
    max_steps: int = Field(default=12, ge=1, le=30)
    run_id: str | None = None


def register_desktop_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
) -> None:
    @router.get("/desktop/tools")
    async def list_desktop_tools() -> dict[str, Any]:
        """Return the current Computer Use tool surface + policy snapshot.

        Read-only — no ``guard`` dependency so the UI can render capability
        state before authenticating a write.
        """
        tools = plugin.build_computer_tool_map()
        cu = plugin.effective_computer_use_config()
        return {
            "policy": {
                "enabled": cu.enabled,
                "allow_mouse": cu.allow_mouse,
                "allow_keyboard": cu.allow_keyboard,
                "allow_screenshot": cu.allow_screenshot,
                "allow_shell": cu.allow_shell,
                "allow_filesystem": cu.allow_filesystem,
                "allowed_shell_commands": list(cu.allowed_shell_commands),
                "filesystem_root": cu.filesystem_root,
                "filesystem_max_bytes": cu.filesystem_max_bytes,
                "shell_timeout_seconds": cu.shell_timeout_seconds,
                "audit_log_path": cu.audit_log_path,
                "require_approval_for": list(cu.require_approval_for),
                "approval_timeout_seconds": cu.approval_timeout_seconds,
            },
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["input_schema"],
                }
                for t in tools.values()
            ],
        }

    @router.post("/desktop/tools/{tool_name}", dependencies=[Depends(guard)])
    async def invoke_desktop_tool(
        tool_name: str, payload: DesktopToolInvokeRequest, request: Request
    ) -> dict[str, Any]:
        """Invoke a single Computer Use tool by name.

        Returns the tool's own ``{"status": "success"|"denied"|"error"}``
        envelope verbatim — capability gating stays enforced inside the
        handler, so the API layer only rate-limits and logs.
        """
        enforce(token_rate_limit, request, f"desktop_tool:{tool_name}")
        tools = plugin.build_computer_tool_map()
        spec = tools.get(tool_name)
        if spec is None:
            raise HTTPException(
                status_code=404, detail=f"unknown computer-use tool: {tool_name}"
            )
        handler = spec["handler"]
        try:
            result = await handler(**payload.args)
        except TypeError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid arguments: {exc}"
            ) from exc
        _BUS.publish(
            "desktop.tool_invoked",
            {
                "tool": tool_name,
                "status": result.get("status") if isinstance(result, dict) else None,
            },
        )
        return {"tool": tool_name, "result": result}

    @router.post("/desktop/task", dependencies=[Depends(guard)])
    async def dispatch_desktop_task(
        payload: DesktopTaskRequest, request: Request
    ) -> dict[str, Any]:
        """Launch a natural-language desktop agent run in the background.

        Returns the ``run_id`` immediately; progress + terminal state are
        published on the event bus (``desktop.run.*``) and can be polled
        via ``GET /desktop/task/{run_id}``.
        """
        enforce(token_rate_limit, request, "desktop_task_dispatch")
        policy = plugin.effective_computer_use_config()
        if not policy.enabled:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Computer Use master switch is off. Arm it from the "
                    "Computer Use page before launching a desktop task."
                ),
            )

        active_run_id = plugin.desktop_active_run_id()
        if active_run_id is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"desktop run {active_run_id!r} is already in flight; "
                    "stop it before launching another (session lane busy)."
                ),
            )

        run_id = payload.run_id or f"desk-{uuid4().hex[:12]}"
        tracker = plugin.desktop_run_tracker
        tracker.start(
            run_id=run_id,
            goal=payload.goal,
            start_url=None,
            max_steps=payload.max_steps,
            extract_fields=[],
        )
        _BUS.publish(
            "desktop.run.started",
            {"run_id": run_id, "goal": payload.goal, "max_steps": payload.max_steps},
        )

        cancel_event = await plugin.register_desktop_cancel(run_id)
        asyncio.create_task(
            _execute_desktop_task(
                plugin=plugin,
                run_id=run_id,
                goal=payload.goal,
                max_steps=payload.max_steps,
                cancel_event=cancel_event,
            )
        )
        return {"run_id": run_id, "status": "running", "started_at": time.time()}

    @router.post("/desktop/task/{run_id}/cancel", dependencies=[Depends(guard)])
    async def cancel_desktop_task(run_id: str, request: Request) -> dict[str, Any]:
        """Signal a running desktop task to stop at the next safe boundary.

        The agent finishes the tool call currently in flight, then the next
        loop iteration observes the cancel event and aborts with
        ``error="cancelled by operator"``. Returns 404 when the run is not
        active (already finished or never existed).
        """
        enforce(token_rate_limit, request, "desktop_task_cancel")
        signalled = await plugin.cancel_desktop_run(run_id)
        if not signalled:
            raise HTTPException(
                status_code=404,
                detail="no active desktop run with that id",
            )
        _BUS.publish("desktop.run.cancel_requested", {"run_id": run_id})
        return {"run_id": run_id, "cancel_requested": True}

    @router.get("/desktop/task/latest")
    async def desktop_task_latest() -> dict[str, Any]:
        state = plugin.desktop_run_tracker.latest()
        return {"run": state.model_dump() if state is not None else None}

    @router.get("/desktop/task/recent")
    async def desktop_task_recent(limit: int = 8) -> dict[str, Any]:
        runs = plugin.desktop_run_tracker.recent(limit=limit)
        return {"runs": [run.model_dump() for run in runs]}

    @router.get("/desktop/task/{run_id}")
    async def desktop_task_detail(run_id: str) -> dict[str, Any]:
        state = plugin.desktop_run_tracker.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="desktop run not found")
        return {"run": state.model_dump()}


async def _execute_desktop_task(
    *,
    plugin: "BaselithbotPlugin",
    run_id: str,
    goal: str,
    max_steps: int,
    cancel_event: asyncio.Event,
) -> None:
    """Drive the DesktopAgent loop, mirror progress into the run tracker + bus."""
    tracker = plugin.desktop_run_tracker
    history: list[str] = []
    last_screenshot: str | None = None
    started_at = time.time()

    async def on_progress(payload: dict[str, Any]) -> None:
        nonlocal last_screenshot
        if payload.get("last_screenshot_b64"):
            last_screenshot = str(payload["last_screenshot_b64"])
        entry = (
            f"{payload.get('tool', '?')}"
            f"({payload.get('args')}) -> "
            f"{payload.get('status', '?')}"
        )
        history.append(entry)
        tracker.step(
            run_id,
            steps_taken=int(payload.get("step", 0)),
            current_url="",
            action=str(payload.get("tool", "")),
            reasoning=str(payload.get("reasoning", "")),
            history=list(history),
            extracted_data={},
            last_screenshot_b64=last_screenshot,
        )
        _BUS.publish(
            "desktop.run.step",
            {
                "run_id": run_id,
                "step": payload.get("step"),
                "tool": payload.get("tool"),
                "status": payload.get("status"),
                "reasoning": payload.get("reasoning", ""),
                "result_summary": payload.get("result_summary", ""),
            },
        )

    try:
        async with plugin.desktop_run_lane:
            plugin.set_desktop_active_run(run_id)
            agent = plugin.create_desktop_agent()
            result = await agent.execute(
                goal=goal,
                max_steps=max_steps,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
        plugin.usage.record(
            UsageEvent(
                agent_id="baselithbot_desktop",
                channel="desktop",
                model=f"{result.provider}/{result.model}"
                if result.provider and result.model
                else result.model,
                completion_tokens=result.tokens_used,
                total_tokens=result.tokens_used,
                latency_ms=(time.time() - started_at) * 1000.0,
                metadata={
                    "run_id": run_id,
                    "goal": goal[:200],
                    "success": result.success,
                    "steps_taken": result.steps_taken,
                },
            )
        )
        tracker.finish(
            run_id,
            success=result.success,
            final_url="",
            steps_taken=result.steps_taken,
            extracted_data={},
            history=[
                f"#{s.step} {s.tool} -> {s.status}: {s.result_summary[:80]}"
                for s in result.history
            ],
            error=result.error,
            last_screenshot_b64=last_screenshot,
        )
        _BUS.publish(
            "desktop.run.finished" if result.success else "desktop.run.failed",
            {
                "run_id": run_id,
                "steps_taken": result.steps_taken,
                "success": result.success,
                "final_reasoning": result.final_reasoning,
                "error": result.error,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        plugin.usage.record(
            UsageEvent(
                agent_id="baselithbot_desktop",
                channel="desktop",
                latency_ms=(time.time() - started_at) * 1000.0,
                metadata={"run_id": run_id, "error": str(exc)[:200]},
            )
        )
        tracker.finish(
            run_id,
            success=False,
            final_url="",
            steps_taken=0,
            extracted_data={},
            history=history,
            error=str(exc),
            last_screenshot_b64=last_screenshot,
        )
        _BUS.publish(
            "desktop.run.failed",
            {"run_id": run_id, "error": str(exc)},
        )
    finally:
        await plugin.clear_desktop_cancel(run_id)
        if plugin.desktop_active_run_id() == run_id:
            plugin.set_desktop_active_run(None)


__all__ = ["register_desktop_routes"]
