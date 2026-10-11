"""The request inputs a resumed run needs, captured into its checkpoint.

A checkpoint used to keep only the query. Every resume path — the recovery
sweep, ``POST /approvals/{run_id}/resume``, a direct
``process(run_id=..., resume=True)`` — re-entered the loop with nothing but the
tenant, so a run that started with ``strategy="react"`` and a tool list came
back without either and the reasoning handler quietly ran Tree-of-Thoughts
instead: a different engine answering the same request.

:func:`capture_run_inputs` snapshots the caller's context when a run starts —
a JSON-safe, size-bounded subset, never a secret — and
:func:`restore_run_inputs` puts it back on every resume, filling only the keys
the resuming caller did not supply.

What is kept, and what is not:

* **Kept** — any JSON-serializable value: strategy and engine knobs,
  ``history_text`` / history messages, attachment *references* (paths, URLs,
  names, MIME types), user and conversation identity.
* **Never kept** — keys that name a credential (``token``, ``secret``,
  ``password``, ``api_key``, ``authorization``, … at any nesting depth), raw
  attachment bytes (``attachment_data``, ``image_data``), the live runtime
  objects the orchestrator rebuilds on every attempt, and anything over the
  size bounds. Skipped keys are listed under ``inputs["_dropped"]`` with the
  reason, so an operator can see what a resume will lack.
* **Tools** — callables cannot be persisted. ``react_tools`` and
  ``tool_registry`` are stored as names (plus category/description) and
  resolved on resume through :func:`set_resume_tool_resolver`; a tool that
  cannot be resolved is reported under :data:`UNRESOLVED_TOOLS_KEY` instead of
  silently resuming without it. ``tool_calls`` (plain dataclasses) round-trip.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import re
from collections.abc import Callable
from typing import Any

from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

#: Upper bound on one captured value (encoded JSON bytes).
MAX_INPUT_VALUE_BYTES = 64 * 1024
#: Upper bound on the whole captured set (encoded JSON bytes).
MAX_INPUTS_BYTES = 256 * 1024

#: Context key a resume sets when persisted tools could not be resolved.
UNRESOLVED_TOOLS_KEY = "resume_unresolved_tools"

#: Live objects the orchestrator rebuilds on every attempt; never captured.
#: ``tenant_id`` is on the checkpoint itself and is never restored from
#: inputs: the resume caller binds the tenant, and the isolation guard has
#: already run by the time inputs are restored.
_RUNTIME_KEYS = frozenset(
    {
        "autonomy_policy",
        "checkpoint",
        "contract_validator",
        "feedback_collector",
        "human_intervention",
        "intent",
        "loop_budget",
        "memory_manager",
        "skill_service",
        "skills_catalog",
        "tenant_id",
        "tool_hooks",
        "tool_rate_limiter",
        UNRESOLVED_TOOLS_KEY,
    }
)

#: Raw attachment content: a resume keeps references, not bytes.
_CONTENT_KEYS = frozenset({"attachment_data", "image_data"})

#: Key names that carry a credential, matched per ``_``-separated component.
_SECRET_KEY_RE = re.compile(
    r"(^|_)(token|secret|secrets|password|passwd|pwd|apikey|api_key|key_secret|"
    r"authorization|auth|credential|credentials|cookie|cookies|private_key|"
    r"bearer|signature|session_key)($|_)",
    re.IGNORECASE,
)

_TOOL_REFS = "__tool_refs__"
_TOOL_CALLS = "__tool_calls__"

#: ``name -> tool`` lookup used to rebuild persisted tool lists on resume.
ToolResolver = Callable[[str], Any]
_resolver: ToolResolver | None = None


def set_resume_tool_resolver(resolver: ToolResolver | None) -> None:
    """Install the lookup that turns a persisted tool name back into a tool.

    Called with each name a resumed run needs; return the
    :class:`~baselith.core.reasoning.react.ToolDefinition` (or, for
    ``tool_registry`` entries, a callable) or ``None`` when unknown. ``None``
    uninstalls it. Without a resolver a run that needs tools cannot resume
    them, and says so (see :data:`UNRESOLVED_TOOLS_KEY`).
    """
    global _resolver
    _resolver = resolver


def _is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key.replace("-", "_")))


def _scrub(value: Any) -> Any:
    """Drop credential-named keys at every depth of a dict/list tree."""
    if isinstance(value, dict):
        return {
            k: _scrub(v)
            for k, v in value.items()
            if not (isinstance(k, str) and _is_secret_key(k))
        }
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def _tool_refs(value: Any) -> dict[str, Any] | None:
    """``react_tools`` / ``tool_registry`` as persisted name references."""
    refs: list[dict[str, Any]] = []
    if isinstance(value, dict):
        refs = [{"name": str(name)} for name in value]
    elif isinstance(value, list | tuple):
        for tool in value:
            name = getattr(tool, "name", None)
            if not isinstance(name, str):
                return None
            ref: dict[str, Any] = {"name": name}
            for attr in ("category", "description"):
                extra = getattr(tool, attr, None)
                if isinstance(extra, str):
                    ref[attr] = extra
            refs.append(ref)
    else:
        return None
    return {_TOOL_REFS: refs}


def _tool_calls(value: Any) -> dict[str, Any] | None:
    """``tool_calls`` dataclasses as plain dicts (enums by value)."""
    if not isinstance(value, list | tuple):
        return None
    calls: list[dict[str, Any]] = []
    for call in value:
        if not dataclasses.is_dataclass(call) or isinstance(call, type):
            return None
        row = dataclasses.asdict(call)
        calls.append(
            {k: (v.value if isinstance(v, enum.Enum) else v) for k, v in row.items()}
        )
    return {_TOOL_CALLS: calls}


def _encode(value: Any) -> bytes | None:
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=False).encode()
    except (TypeError, ValueError):
        return None


def capture_run_inputs(context: dict[str, Any]) -> dict[str, Any]:
    """Snapshot the resumable part of a run's context (see module docstring).

    Args:
        context: The orchestration context as the caller passed it.

    Returns:
        A JSON-safe dict; ``"_dropped"`` maps each skipped key to the reason.
    """
    captured: dict[str, Any] = {}
    dropped: dict[str, str] = {}
    total = 0
    for key, value in context.items():
        if not isinstance(key, str) or key.startswith("_") or key in _RUNTIME_KEYS:
            continue
        if _is_secret_key(key):
            dropped[key] = "secret"
            continue
        if key in _CONTENT_KEYS:
            dropped[key] = "content"
            continue
        if key in ("react_tools", "tool_registry"):
            candidate: Any = _tool_refs(value)
        elif key == "tool_calls":
            candidate = _tool_calls(value)
        else:
            candidate = _scrub(value)
        encoded = _encode(candidate) if candidate is not None else None
        if encoded is None:
            dropped[key] = "unserializable"
            continue
        if len(encoded) > MAX_INPUT_VALUE_BYTES or total + len(encoded) > (
            MAX_INPUTS_BYTES
        ):
            dropped[key] = "too_large"
            continue
        total += len(encoded)
        # Store the decoded copy: the checkpoint must not alias caller objects.
        captured[key] = json.loads(encoded)
    if dropped:
        captured["_dropped"] = dropped
    return captured


def _resolve_tools(refs: list[dict[str, Any]], registry: bool) -> tuple[Any, list[str]]:
    resolved: list[tuple[str, Any]] = []
    missing: list[str] = []
    for ref in refs:
        name = str(ref.get("name", ""))
        tool = None
        if _resolver is not None:
            try:
                tool = _resolver(name)
            except Exception as exc:
                logger.warning(
                    "resume_tool_resolver_failed tool=%s error=%s", name, exc
                )
        if tool is None:
            missing.append(name)
        else:
            resolved.append((name, tool))
    if registry:
        return {n: getattr(t, "fn", t) for n, t in resolved}, missing
    return [t for _, t in resolved], missing


def _rebuild_tool_calls(rows: list[dict[str, Any]]) -> list[Any]:
    from baselith.core.orchestration.parallel import ToolCall, ToolStatus

    calls: list[Any] = []
    for row in rows:
        data = dict(row)
        if "status" in data:
            data["status"] = ToolStatus(data["status"])
        calls.append(ToolCall(**data))
    return calls


def restore_run_inputs(context: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    """Put captured inputs back on a resuming run's context.

    Only keys the resuming caller left unset are filled — an explicit value
    on the resume call always wins. Tool lists are rebuilt through the
    installed resolver; names it cannot resolve are listed under
    :data:`UNRESOLVED_TOOLS_KEY` and the tool list itself is left unset.

    Returns:
        The restored keys, in capture order.
    """
    restored: list[str] = []
    unresolved: list[str] = []
    for key, value in (inputs or {}).items():
        if key.startswith("_") or key in _RUNTIME_KEYS or key in context:
            continue
        if isinstance(value, dict) and _TOOL_REFS in value:
            tools, missing = _resolve_tools(
                list(value[_TOOL_REFS]), registry=key == "tool_registry"
            )
            if missing:
                unresolved.extend(missing)
                continue
            context[key] = tools
        elif isinstance(value, dict) and _TOOL_CALLS in value:
            try:
                context[key] = _rebuild_tool_calls(list(value[_TOOL_CALLS]))
            except (TypeError, ValueError) as exc:
                logger.warning("resume_tool_calls_unrestorable error=%s", exc)
                continue
        else:
            context[key] = value
        restored.append(key)
    if unresolved:
        context[UNRESOLVED_TOOLS_KEY] = sorted(set(unresolved))
        logger.warning(
            "resume_tools_unresolved tools=%s (install set_resume_tool_resolver)",
            context[UNRESOLVED_TOOLS_KEY],
        )
    return restored


__all__ = [
    "MAX_INPUTS_BYTES",
    "MAX_INPUT_VALUE_BYTES",
    "UNRESOLVED_TOOLS_KEY",
    "capture_run_inputs",
    "restore_run_inputs",
    "set_resume_tool_resolver",
]
