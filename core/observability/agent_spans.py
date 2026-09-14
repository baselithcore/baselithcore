"""Agent-attributed spans — who ran, under whom, with which tool.

Every other span in the framework says *what* happened (a chat completion, an
HTTP call, a skill activation) but never *which agent* was running when it
happened. That gap is why the trace waterfall can show a slow multi-agent answer
without being able to say which agent owned the slow part, and why a topology
view of "who calls whom" cannot be built from the span stream at all.

This module closes it with two context managers that wrap a unit of agent work
and stamp it with the OpenTelemetry GenAI semantic conventions, so semconv-aware
backends (Langfuse, Phoenix, Datadog LLM Observability, plain OTel collectors)
light up on the same data the in-house dashboard reads:

``gen_ai.operation.name``
    ``invoke_agent`` for :func:`agent_span`, ``execute_tool`` for :func:`tool_span`.
``gen_ai.agent.name`` / ``gen_ai.agent.id``
    Display name and stable id. The id is ``<plugin>:<name>`` when an owning
    plugin is known, so two plugins may ship an agent of the same name.
``gen_ai.tool.name``
    Tool name, on tool spans only.
``baselith.agent.kind``
    Which seam produced it — ``handler``, ``swarm``, ``crew``, ``workflow``,
    ``tool``. Extension namespace, not semconv.
``baselith.agent.parent``
    The enclosing agent's id, read from a context variable rather than from span
    nesting. Span parentage records whichever span happens to enclose this one;
    the context variable records the enclosing *agent*, which is the edge a
    topology view actually needs and which survives unrelated spans in between.
``baselith.plugin``
    The owning plugin, which is also the primary key the trace store's plugin
    attribution reads.

Nothing here is domain-specific and nothing imports a plugin: it is span
plumbing over :mod:`core.observability.tracing`. Attribution is best-effort by
construction — a failure to record telemetry must never fail the work being
recorded, so the context managers always yield and always restore the context
variable.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from typing import Any

from core.observability.tracing import Span, get_tracer

__all__ = [
    "AGENT_ID_KEY",
    "AGENT_KIND_KEY",
    "AGENT_NAME_KEY",
    "AGENT_PARENT_KEY",
    "OPERATION_KEY",
    "PLUGIN_KEY",
    "TOOL_NAME_KEY",
    "agent_id_for",
    "agent_span",
    "current_agent_id",
    "current_agent_name",
    "tool_span",
]

# OTel GenAI semantic conventions.
OPERATION_KEY = "gen_ai.operation.name"
AGENT_NAME_KEY = "gen_ai.agent.name"
AGENT_ID_KEY = "gen_ai.agent.id"
TOOL_NAME_KEY = "gen_ai.tool.name"

# Baselith extension namespace (not semconv; prefixed to stay out of its way).
AGENT_KIND_KEY = "baselith.agent.kind"
AGENT_PARENT_KEY = "baselith.agent.parent"
PLUGIN_KEY = "baselith.plugin"

_INVOKE_AGENT = "invoke_agent"
_EXECUTE_TOOL = "execute_tool"

# Tracer name doubles as the span's ``service.name`` attribute downstream, so it
# is deliberately generic: these spans are framework plumbing, not a service.
_TRACER = "agent"

# The agent currently executing, as ``(agent_id, agent_name)``. A context
# variable rather than a thread local: agent work is async, and a contextvar is
# the only carrier that follows an ``await`` into a task without leaking across
# concurrent sibling tasks.
_CURRENT: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "baselith_current_agent", default=None
)


def agent_id_for(name: str, plugin: str | None = None) -> str:
    """Return the stable id for an agent named *name* owned by *plugin*.

    Two plugins may each ship an agent called ``researcher``; qualifying the id
    with the owner keeps them distinct nodes in a topology view while leaving
    the display name short. Core-owned agents get the ``core:`` prefix so every
    id has the same shape and can be split on the first colon.

    Args:
        name: Agent display name.
        plugin: Owning plugin, or ``None`` for a core-owned agent.

    Returns:
        ``"<plugin>:<name>"``, with ``core`` standing in for an unowned agent.
    """
    return f"{plugin or 'core'}:{name}"


def current_agent_id() -> str | None:
    """Return the executing agent's id, or ``None`` outside any agent."""
    current = _CURRENT.get()
    return current[0] if current else None


def current_agent_name() -> str | None:
    """Return the executing agent's display name, or ``None`` outside any."""
    current = _CURRENT.get()
    return current[1] if current else None


def _attributes(
    *,
    operation: str,
    name: str,
    agent_id: str,
    kind: str,
    plugin: str | None,
    parent: str | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the attribute bag for one agent or tool span."""
    attributes: dict[str, Any] = {
        OPERATION_KEY: operation,
        AGENT_NAME_KEY: name,
        AGENT_ID_KEY: agent_id,
        AGENT_KIND_KEY: kind,
    }
    if plugin:
        attributes[PLUGIN_KEY] = plugin
    if parent:
        attributes[AGENT_PARENT_KEY] = parent
    if extra:
        attributes.update(extra)
    return attributes


@contextlib.contextmanager
def agent_span(
    name: str,
    *,
    plugin: str | None = None,
    kind: str = "handler",
    agent_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span | None]:
    """Record one agent invocation, and make it the current agent while it runs.

    The span is named ``invoke_agent <name>`` to match the GenAI convention of
    prefixing the span name with the operation. An exception propagates
    untouched after the span is marked failed.

    Args:
        name: Agent display name (an intent, a sub-agent role, a crew member).
        plugin: Owning plugin, when known.
        kind: Producing seam — ``handler``, ``swarm``, ``crew``, ``workflow``.
        agent_id: Explicit id; derived from *name* and *plugin* when omitted.
        attributes: Extra span attributes to merge in.

    Yields:
        The span, or ``None`` when telemetry could not be started. Callers must
        tolerate ``None``: recording must never be load-bearing.
    """
    resolved_id = agent_id or agent_id_for(name, plugin)
    parent = current_agent_id()
    token = _CURRENT.set((resolved_id, name))
    try:
        bag = _attributes(
            operation=_INVOKE_AGENT,
            name=name,
            agent_id=resolved_id,
            kind=kind,
            plugin=plugin,
            parent=parent,
            extra=attributes,
        )
        with _span(f"{_INVOKE_AGENT} {name}", bag) as span:
            yield span
    finally:
        _CURRENT.reset(token)


@contextlib.contextmanager
def tool_span(
    tool_name: str,
    *,
    plugin: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span | None]:
    """Record one tool call, attributed to the agent that made it.

    Unlike :func:`agent_span` this does **not** become the current agent: a tool
    is a leaf, and treating it as an agent would make every tool a node in the
    topology and every subsequent call appear to descend from it.

    Args:
        tool_name: Tool being called.
        plugin: Owning plugin, when known.
        attributes: Extra span attributes to merge in.

    Yields:
        The span, or ``None`` when telemetry could not be started.
    """
    current = _CURRENT.get()
    agent_id, agent_name = current if current else ("core:unattributed", "unattributed")
    bag = _attributes(
        operation=_EXECUTE_TOOL,
        name=agent_name,
        agent_id=agent_id,
        kind="tool",
        plugin=plugin,
        parent=agent_id,
        extra={TOOL_NAME_KEY: tool_name, **(attributes or {})},
    )
    with _span(f"{_EXECUTE_TOOL} {tool_name}", bag) as span:
        yield span


@contextlib.contextmanager
def _span(span_name: str, attributes: dict[str, Any]) -> Iterator[Span | None]:
    """Open a tracer span, degrading to a no-op yield when tracing is unusable.

    Two failure modes are swallowed here, and only here: the tracer refusing to
    start (telemetry disabled mid-process, a misconfigured exporter) and the
    span failing to close. Either would otherwise turn an observability concern
    into a request failure.

    Errors raised by the *body* are not swallowed. They propagate, and the
    tracer's own ``start_span`` marks the span failed and records the exception
    event on the way past, so this wrapper does not duplicate that.
    """
    try:
        manager = get_tracer(_TRACER).start_span(span_name, attributes=attributes)
        span = manager.__enter__()
    except Exception:
        yield None
        return

    failure: BaseException | None = None
    try:
        yield span
    except BaseException as exc:
        failure = exc
        raise
    finally:
        # Closing is best-effort: an exporter that throws on flush must not
        # replace the body's exception (or invent one where there was none).
        with contextlib.suppress(Exception):
            manager.__exit__(
                type(failure) if failure else None,
                failure,
                failure.__traceback__ if failure else None,
            )
