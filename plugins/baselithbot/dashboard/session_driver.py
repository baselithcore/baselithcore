"""Session reply dispatcher — slash commands (sync) + browser tasks (async).

When a user sends a message via ``POST /sessions/{sid}/send``, this module
appends the assistant reply to the session and publishes dashboard events.
Slash commands (``/cmd``) run inline; plain text launches a background
``BaselithbotAgent`` run tracked via ``plugin.run_tracker``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.observability.logging import get_logger

from plugins.baselithbot.sessions.manager import SessionMessage
from plugins.baselithbot.models import BaselithbotTask
from plugins.baselithbot.observability.usage import UsageEvent
from plugins.baselithbot.dashboard.bus import _BUS

if TYPE_CHECKING:
    from plugins.baselithbot.plugin import BaselithbotPlugin

_log = get_logger(__name__)


def _format_slash_reply(line: str, result: dict[str, Any]) -> str:
    status = result.get("status", "ok")
    if status == "unknown":
        supported = ", ".join(f"/{c}" for c in result.get("supported", []))
        return f"Unknown command `{line}`. Supported: {supported}"
    if status == "error":
        return f"Command error: {result.get('error', 'unknown')}"
    try:
        body = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        body = str(result)
    return f"```json\n{body}\n```"


async def drive_session_reply(
    plugin: "BaselithbotPlugin", sid: str, user_text: str
) -> dict[str, Any]:
    """Dispatch a user message: slash command (sync) or browser task (async).

    Appends the assistant reply to the session history and publishes the
    corresponding bus event so the dashboard UI updates in real time.
    """
    text = user_text.strip()
    if text.startswith("/"):
        try:
            result = await plugin.chat_commands.handle(text, {"session_id": sid})
        except Exception as exc:
            _log.warning("session_slash_handler_failed", error=str(exc))
            result = {"status": "error", "error": str(exc)}
        reply_text = _format_slash_reply(text, result)
        reply = plugin.sessions.send(
            sid,
            SessionMessage(
                role="assistant",
                content=reply_text,
                metadata={"kind": "slash", "command": text, "result": result},
            ),
        )
        _BUS.publish("session.message", {"session_id": sid, **reply.model_dump()})
        return {"kind": "slash", "result": result}

    if not text:
        return {"kind": "none"}

    run_id = f"run-{uuid4().hex[:12]}"
    max_steps = int(plugin._agent_config.get("max_steps", 20))
    plugin.run_tracker.start(
        run_id=run_id,
        goal=text,
        start_url=None,
        max_steps=max_steps,
        extract_fields=[],
    )
    ack = plugin.sessions.send(
        sid,
        SessionMessage(
            role="assistant",
            content=f"Task launched (run `{run_id}`) — goal: {text[:400]}",
            metadata={
                "kind": "task_ack",
                "run_id": run_id,
                "status": "running",
                "goal": text,
            },
        ),
    )
    _BUS.publish("session.message", {"session_id": sid, **ack.model_dump()})
    _BUS.publish(
        "run.started",
        {
            "run_id": run_id,
            "goal": text,
            "max_steps": max_steps,
            "start_url": None,
            "session_id": sid,
        },
    )
    asyncio.create_task(_run_session_task(plugin, sid, run_id, text, max_steps))
    return {"kind": "task", "run_id": run_id}


async def _run_session_task(
    plugin: "BaselithbotPlugin",
    sid: str,
    run_id: str,
    goal: str,
    max_steps: int,
) -> None:
    """Execute a BaselithbotAgent task in the background, report via session."""
    started_at = time.time()
    try:
        agent = await plugin.get_or_start_agent()
        task = BaselithbotTask(goal=goal, max_steps=max_steps)

        async def _on_progress(payload: dict[str, Any]) -> None:
            state = plugin.run_tracker.step(
                run_id,
                steps_taken=int(payload.get("steps_taken", 0)),
                current_url=str(payload.get("current_url", "")),
                action=str(payload.get("action", "")),
                reasoning=str(payload.get("reasoning", "")),
                history=list(payload.get("history", [])),
                extracted_data=dict(payload.get("extracted_data", {})),
                last_screenshot_b64=payload.get("last_screenshot_b64"),
            )
            if state is None:
                return
            _BUS.publish(
                "run.step",
                {
                    "run_id": run_id,
                    "session_id": sid,
                    "steps_taken": state.steps_taken,
                    "action": state.last_action,
                    "reasoning": state.last_reasoning,
                    "current_url": state.current_url,
                },
            )

        result = await agent.execute(
            task, context={"run_id": run_id, "on_progress": _on_progress}
        )
        plugin.usage.record(
            UsageEvent(
                session_id=sid,
                agent_id=agent.agent_id,
                model=f"{result.provider}/{result.model}"
                if result.provider and result.model
                else result.model,
                completion_tokens=result.tokens_used,
                total_tokens=result.tokens_used,
                latency_ms=(time.time() - started_at) * 1000.0,
                metadata={"run_id": run_id, "goal": goal[:200]},
            )
        )
        plugin.run_tracker.finish(
            run_id,
            success=result.success,
            final_url=result.final_url,
            steps_taken=result.steps_taken,
            extracted_data=result.extracted_data,
            history=result.history,
            error=result.error,
            last_screenshot_b64=result.last_screenshot_b64,
        )
        summary = _format_task_summary(result.success, result, goal)
        reply = plugin.sessions.send(
            sid,
            SessionMessage(
                role="assistant",
                content=summary,
                metadata={
                    "kind": "task_result",
                    "run_id": run_id,
                    "status": "completed" if result.success else "failed",
                    "final_url": result.final_url,
                    "steps_taken": result.steps_taken,
                    "extracted_data": result.extracted_data,
                    "error": result.error,
                },
            ),
        )
        _BUS.publish("session.message", {"session_id": sid, **reply.model_dump()})
        _BUS.publish(
            "run.completed" if result.success else "run.failed",
            {
                "run_id": run_id,
                "session_id": sid,
                "steps_taken": result.steps_taken,
                "final_url": result.final_url,
                "error": result.error,
            },
        )
    except Exception as exc:
        _log.exception("session_task_failed", run_id=run_id, sid=sid)
        plugin.usage.record(
            UsageEvent(
                session_id=sid,
                agent_id="baselithbot",
                latency_ms=(time.time() - started_at) * 1000.0,
                metadata={"run_id": run_id, "error": str(exc)[:200]},
            )
        )
        plugin.run_tracker.finish(
            run_id,
            success=False,
            final_url="",
            steps_taken=0,
            extracted_data={},
            history=[],
            error=str(exc),
            last_screenshot_b64=None,
        )
        try:
            reply = plugin.sessions.send(
                sid,
                SessionMessage(
                    role="assistant",
                    content=f"Task failed: {exc}",
                    metadata={
                        "kind": "task_result",
                        "run_id": run_id,
                        "status": "failed",
                        "error": str(exc),
                    },
                ),
            )
            _BUS.publish("session.message", {"session_id": sid, **reply.model_dump()})
        except Exception:
            pass
        _BUS.publish(
            "run.failed",
            {"run_id": run_id, "session_id": sid, "error": str(exc)},
        )


def _format_task_summary(success: bool, result: Any, goal: str) -> str:
    tag = "completed" if success else "failed"
    parts: list[str] = [
        f"Task {tag} — goal: {goal[:400]}",
        f"steps: {result.steps_taken} · final URL: {result.final_url or '—'}",
    ]
    if result.error:
        parts.append(f"error: {result.error}")
    extracted = getattr(result, "extracted_data", None) or {}
    filled = {k: v for k, v in extracted.items() if v is not None}
    if filled:
        try:
            parts.append(
                "extracted:\n```json\n"
                + json.dumps(filled, indent=2, ensure_ascii=False, default=str)
                + "\n```"
            )
        except (TypeError, ValueError):
            parts.append(f"extracted: {filled}")
    return "\n".join(parts)


__all__ = ["drive_session_reply"]
