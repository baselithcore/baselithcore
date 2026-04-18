"""Additional MCP tools (code edit, usage ledger, process, tailscale, workspace).

Bound to per-plugin shared state. All tool entry points return ``status``
dicts (never raise) so the orchestrator stays in control of error policy.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

from .agents import AgentRegistry, AgentRouter
from .approvals import ApprovalGate
from .code_edit import (
    LineRangeEdit,
    LineRangePatcher,
    MultiFileEdit,
    MultiFileEditor,
    SearchReplaceEdit,
    apply_search_replace,
    apply_unified_diff,
)
from .computer_use import AuditLogger, ComputerUseConfig, ComputerUseError
from .filesystem import ScopedFileSystem
from .gateway import TailscaleProvisioner
from .process_manager import ProcessManager
from .usage import UsageEvent, UsageLedger
from .workspace import WorkspaceConfig, WorkspaceManager

logger = get_logger(__name__)


def _denied(exc: ComputerUseError) -> dict[str, Any]:
    return {"status": "denied", "error": str(exc)}


def _error(tool: str, exc: Exception) -> dict[str, Any]:
    logger.error("baselithbot_extra_tool_error", tool=tool, error=str(exc))
    return {"status": "error", "error": str(exc)}


def build_extra_tool_definitions(
    *,
    config: ComputerUseConfig,
    usage: UsageLedger | None = None,
    workspaces: WorkspaceManager | None = None,
    agents: AgentRegistry | None = None,
    approvals: ApprovalGate | None = None,
) -> list[dict[str, Any]]:
    """Return code-edit + usage + process + tailscale + workspace MCP tools."""
    audit = AuditLogger(config.audit_log_path)
    fs = ScopedFileSystem(config, audit, approvals=approvals)
    process_mgr = ProcessManager(config, audit)
    usage_ledger: UsageLedger = usage if usage is not None else UsageLedger()
    workspace_mgr: WorkspaceManager = (
        workspaces if workspaces is not None else WorkspaceManager()
    )
    agent_registry: AgentRegistry = agents if agents is not None else AgentRegistry()
    agent_router = AgentRouter(agent_registry)

    # ---------------- Code edit ----------------

    async def code_diff_apply(diff_text: str) -> dict[str, Any]:
        try:
            return await apply_unified_diff(diff_text, fs)
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("code_diff_apply", exc)

    async def code_line_edit(edits: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            patcher = LineRangePatcher(fs)
            parsed = [LineRangeEdit.model_validate(e) for e in edits]
            return await patcher.apply(parsed)
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("code_line_edit", exc)

    async def code_search_replace(
        path: str,
        pattern: str,
        replacement: str,
        regex: bool = False,
        count: int = 0,
        case_insensitive: bool = False,
    ) -> dict[str, Any]:
        try:
            return await apply_search_replace(
                SearchReplaceEdit(
                    path=path,
                    pattern=pattern,
                    replacement=replacement,
                    regex=regex,
                    count=count,
                    case_insensitive=case_insensitive,
                ),
                fs,
            )
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("code_search_replace", exc)

    async def code_multi_file_write(files: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            editor = MultiFileEditor(fs)
            return await editor.apply([MultiFileEdit.model_validate(f) for f in files])
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("code_multi_file_write", exc)

    # ---------------- Usage ----------------

    async def usage_record(
        session_id: str | None = None,
        agent_id: str | None = None,
        channel: str | None = None,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        usage_ledger.record(
            UsageEvent(
                session_id=session_id,
                agent_id=agent_id,
                channel=channel,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                metadata=metadata or {},
            )
        )
        return {"status": "success"}

    async def usage_summary() -> dict[str, Any]:
        return {
            "status": "success",
            **usage_ledger.summary(),
            "by_model": usage_ledger.by_model_breakdown(),
        }

    async def usage_by_session(session_id: str) -> dict[str, Any]:
        return {"status": "success", **usage_ledger.by_session(session_id)}

    # ---------------- Process management ----------------

    async def process_list(limit: int = 200) -> dict[str, Any]:
        try:
            entries = await process_mgr.list_processes(limit=limit)
            return {"status": "success", "count": len(entries), "processes": entries}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("process_list", exc)

    async def process_kill(pid: int, signal_num: int = 15) -> dict[str, Any]:
        try:
            return await process_mgr.kill(pid=pid, sig=signal_num)
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("process_kill", exc)

    # ---------------- Tailscale provisioning ----------------

    async def tailscale_up(
        auth_key: str | None = None,
        ssh: bool = False,
        accept_routes: bool = False,
        hostname: str | None = None,
    ) -> dict[str, Any]:
        return await TailscaleProvisioner.up(
            auth_key=auth_key,
            ssh=ssh,
            accept_routes=accept_routes,
            hostname=hostname,
        )

    async def tailscale_down() -> dict[str, Any]:
        return await TailscaleProvisioner.down()

    async def tailscale_logout() -> dict[str, Any]:
        return await TailscaleProvisioner.logout()

    # ---------------- Workspaces ----------------

    async def workspace_create(
        name: str, description: str = "", primary: bool = False
    ) -> dict[str, Any]:
        try:
            ws = workspace_mgr.create(
                WorkspaceConfig(name=name, description=description, primary=primary)
            )
            return {"status": "success", "workspace": ws.runtime_summary()}
        except ValueError as exc:
            return {"status": "exists", "error": str(exc)}

    async def workspace_list() -> dict[str, Any]:
        return {
            "status": "success",
            "workspaces": [w.runtime_summary() for w in workspace_mgr.list()],
        }

    async def workspace_remove(name: str) -> dict[str, Any]:
        existed = workspace_mgr.remove(name)
        return {"status": "success" if existed else "not_found", "name": name}

    # ---------------- Multi-agent routing ----------------

    async def agent_list() -> dict[str, Any]:
        return {
            "status": "success",
            "agents": [a.model_dump() for a in agent_registry.list()],
        }

    async def agent_route(query: str) -> dict[str, Any]:
        decision = agent_router.decide(query)
        return {"status": "success", "decision": decision.model_dump()}

    return [
        {
            "name": "baselithbot_code_diff_apply",
            "description": "Apply a unified-diff patch to files under filesystem_root.",
            "input_schema": {
                "type": "object",
                "properties": {"diff_text": {"type": "string"}},
                "required": ["diff_text"],
            },
            "handler": code_diff_apply,
        },
        {
            "name": "baselithbot_code_line_edit",
            "description": "Replace 1-indexed inclusive line ranges in files.",
            "input_schema": {
                "type": "object",
                "properties": {"edits": {"type": "array"}},
                "required": ["edits"],
            },
            "handler": code_line_edit,
        },
        {
            "name": "baselithbot_code_search_replace",
            "description": "Literal or regex search/replace on a single file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                    "replacement": {"type": "string"},
                    "regex": {"type": "boolean", "default": False},
                    "count": {"type": "integer", "default": 0},
                    "case_insensitive": {"type": "boolean", "default": False},
                },
                "required": ["path", "pattern", "replacement"],
            },
            "handler": code_search_replace,
        },
        {
            "name": "baselithbot_code_multi_file_write",
            "description": "Atomic multi-file write with rollback on failure.",
            "input_schema": {
                "type": "object",
                "properties": {"files": {"type": "array"}},
                "required": ["files"],
            },
            "handler": code_multi_file_write,
        },
        {
            "name": "baselithbot_usage_record",
            "description": "Append a usage event (token/cost/latency) to the ledger.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "channel": {"type": "string"},
                    "model": {"type": "string"},
                    "prompt_tokens": {"type": "integer", "default": 0},
                    "completion_tokens": {"type": "integer", "default": 0},
                    "cost_usd": {"type": "number", "default": 0},
                    "latency_ms": {"type": "number", "default": 0},
                    "metadata": {"type": "object"},
                },
            },
            "handler": usage_record,
        },
        {
            "name": "baselithbot_usage_summary",
            "description": "Return aggregate token + cost + per-model breakdown.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": usage_summary,
        },
        {
            "name": "baselithbot_usage_by_session",
            "description": "Return ledger aggregates filtered by session id.",
            "input_schema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
            "handler": usage_by_session,
        },
        {
            "name": "baselithbot_process_list",
            "description": "List running processes (psutil). Requires allow_shell.",
            "input_schema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 200}},
            },
            "handler": process_list,
        },
        {
            "name": "baselithbot_process_kill",
            "description": "Send a signal to a PID. Requires allow_shell.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pid": {"type": "integer"},
                    "signal_num": {"type": "integer", "default": 15},
                },
                "required": ["pid"],
            },
            "handler": process_kill,
        },
        {
            "name": "baselithbot_tailscale_up",
            "description": "Run `tailscale up` with optional auth-key / ssh / hostname.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "auth_key": {"type": "string"},
                    "ssh": {"type": "boolean", "default": False},
                    "accept_routes": {"type": "boolean", "default": False},
                    "hostname": {"type": "string"},
                },
            },
            "handler": tailscale_up,
        },
        {
            "name": "baselithbot_tailscale_down",
            "description": "Run `tailscale down`.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": tailscale_down,
        },
        {
            "name": "baselithbot_tailscale_logout",
            "description": "Run `tailscale logout`.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": tailscale_logout,
        },
        {
            "name": "baselithbot_workspace_create",
            "description": "Create a new isolated workspace.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "primary": {"type": "boolean", "default": False},
                },
                "required": ["name"],
            },
            "handler": workspace_create,
        },
        {
            "name": "baselithbot_workspace_list",
            "description": "List configured workspaces.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": workspace_list,
        },
        {
            "name": "baselithbot_workspace_remove",
            "description": "Delete a workspace and its isolated state.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            "handler": workspace_remove,
        },
        {
            "name": "baselithbot_agent_list",
            "description": "List multi-agent registry entries.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": agent_list,
        },
        {
            "name": "baselithbot_agent_route",
            "description": "Score registered agents against a query and return decision.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "handler": agent_route,
        },
    ]


__all__ = ["build_extra_tool_definitions"]
