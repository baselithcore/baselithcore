"""Everything the typed :class:`~core.agent.agent.Agent` does around a tool
call, except running it.

The loop used to splat model-written arguments straight into a callable and
stringify whatever came back. Four things were missing, and each one is here:

1. **Argument validation** — a hallucinated key or a string where an int
   belongs became an opaque ``TypeError`` *after* the call had consumed a
   budget entry. Validated against the tool's own JSON Schema, it becomes a
   message the model can correct on its next turn.
2. **Gating** — every dispatch goes through
   :func:`core.orchestration.enforcement.enforce_tool_invocation`, the single
   chokepoint that owns the contract check, the plugin-capability check, the
   approval gate, the tool-call budget, the rate limiter, the pre-hooks and the
   audit trail. The loop enforced none of them.
3. **Rendering** — a :class:`~core.plugins.result.SkillResult` becomes its
   ``snapshot`` (not a Pydantic repr) and its ``success`` decides ``is_error``;
   every tool-controlled string is bounded, scanned and sealed in the untrusted
   envelope. This is the *single* seam where that happens: ``wrap_untrusted``
   is deliberately not idempotent, so nothing downstream may re-wrap.
4. **Idempotency** — the ledger claim that keeps an effectful call from
   running twice across a retry of the same ``run_id``.

Split from ``agent.py`` for the file-size cap.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.orchestration.idempotency import ToolCallInFlight
from core.orchestration.tool_output import (
    UNTRUSTED_OUTPUT_SYSTEM_RULE,
    escape_untrusted_markers,
)
from core.reasoning.react_native import infer_tool_parameters
from core.reasoning.react_tool_gate import (
    dispatch_post_hook,
    gate_denial,
    invalid_arguments_message,
    render_observation,
)
from core.services.llm.tool_calling import LLMToolSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.agent.agent import Agent
    from core.reasoning.react import ToolDefinition
    from core.services.llm.tool_calling import ToolCall

logger = get_logger(__name__)

#: Stands in for a tool that succeeded and returned nothing to show.
_EMPTY_RESULT_NOTE = "(tool returned no output)"

__all__ = [
    "build_tool_specs",
    "execute_tool",
    "gate_context",
    "prepare_tool_call",
    "run_prepared_call",
    "system_prompt_for",
]


def build_tool_specs(
    tools: dict[str, ToolDefinition],
) -> list[LLMToolSpec] | None:
    """Describe the agent's tools for the model.

    The schema comes from :meth:`ToolDefinition.json_schema` rather than a
    second call to the inference helper, so the validator, the provider's tool
    definition and this spec all describe the tool identically.

    The autonomy category rides along as MCP-style annotations, derived from
    :meth:`ToolDefinition.normalized_category` — a typo'd category answers
    ``destructive`` there instead of raising deep inside the approval matrix.
    It was invisible before: the fact that decides whether a call needs human
    approval was not surfaced to whatever renders or reviews the tool list.

    Args:
        tools: The agent's tools, keyed by name.

    Returns:
        list[LLMToolSpec] | None: The specs, or None when there are no tools
        (the providers take ``None`` to mean "no tool block at all").
    """
    if not tools:
        return None
    specs: list[LLMToolSpec] = []
    for tool in tools.values():
        category = tool.normalized_category()
        try:
            parameters = tool.json_schema()
        except Exception:  # pragma: no cover - uninspectable callable
            parameters = infer_tool_parameters(tool)
        specs.append(
            LLMToolSpec(
                name=tool.name,
                description=tool.description,
                parameters=parameters,
                annotations={
                    "readOnlyHint": category == "read_only",
                    "destructiveHint": category == "destructive",
                },
            )
        )
    return specs


def system_prompt_for(system_prompt: str | None, has_tools: bool) -> str | None:
    """The system prompt to send, carrying the untrusted-content rule.

    Tool output reaches the model inside
    ``<untrusted_tool_output>`` markers; the envelope without the sentence that
    explains it is decoration. Stated once, and only when the agent has tools —
    a toolless run has nothing untrusted to warn about.

    Args:
        system_prompt: The caller's system prompt, if any.
        has_tools: Whether the agent exposes any tool.

    Returns:
        str | None: The prompt to send, or None when there is nothing to say.
    """
    if not has_tools:
        return system_prompt
    if system_prompt and UNTRUSTED_OUTPUT_SYSTEM_RULE in system_prompt:
        return system_prompt
    if not system_prompt:
        return UNTRUSTED_OUTPUT_SYSTEM_RULE
    return f"{system_prompt}\n\n{UNTRUSTED_OUTPUT_SYSTEM_RULE}"


def _ambient_tenant() -> str | None:
    """The current tenant, or None when unset/unavailable.

    Strict tenant isolation raises when nothing is bound, and an ``Agent.run``
    outside a request (CLI, eval harness, background job) is a normal case; the
    tenant only scopes the rate limiter and the audit record.
    """
    try:
        from core.context import get_current_tenant_id

        return get_current_tenant_id()
    except Exception:  # silent-ok: an unbound tenant is a normal CLI/eval case
        return None


def gate_context(agent: Agent) -> dict[str, Any]:
    """Assemble the orchestration context the enforcement chokepoint reads.

    Each control there is a no-op when its key is absent, so an agent running
    outside an orchestrated request still passes through the shared
    plugin-capability check, rate limiter, pre-hooks and audit trail — which is
    why ``tenant_id`` is always present even when it is ``None``: the
    chokepoint short-circuits on an empty context.

    The budget is picked up *ambiently* because one exists to pick up
    (``budget_context`` publishes the active request's). There is no equivalent
    for the autonomy policy — it is owned by whoever constructed the
    orchestrator or the parallel executor, never published to the async context
    — so the approval gate here is **inert unless a host injects a policy**,
    by setting ``agent._autonomy_policy`` (and optionally
    ``_human_intervention`` / ``_checkpoint``) the way ``_tool_hooks`` is
    injected. Manufacturing a default ``AutonomyPolicy()`` instead would start
    demanding approval for every effectful tool of every existing typed agent,
    with no channel to approve on — a fail-closed break, not a fix.
    """
    from core.orchestration.budget_context import get_active_budget

    context: dict[str, Any] = {"tenant_id": _ambient_tenant()}
    budget = get_active_budget()
    if budget is not None:
        context["loop_budget"] = budget
    for attribute, key in (
        ("_autonomy_policy", "autonomy_policy"),
        ("_human_intervention", "human_intervention"),
        ("_checkpoint", "checkpoint"),
        ("_contract_validator", "contract_validator"),
        ("_tool_hooks", "tool_hooks"),
    ):
        value = getattr(agent, attribute, None)
        if value is not None:
            context[key] = value
    return context


def _renderable(value: Any) -> Any:
    """Normalise a tool's return value for the observation renderer.

    Strings and ``SkillResult``s are handed over untouched (the renderer
    unpacks the latter); anything else is JSON-encoded, which is what the model
    can actually read — ``str(dict)`` is a Python repr.
    """
    from core.plugins.result import SkillResult

    if isinstance(value, str | SkillResult):
        return value
    return json.dumps(value, default=str)


def _runtime_error(message: str) -> tuple[str, bool]:
    """A runtime-authored failure observation (never envelope-wrapped).

    The loop's own narration stays outside the untrusted envelope — teaching
    the model to distrust the runtime would be teaching it to distrust itself —
    so any fragment the tool or the model controls is scrubbed of envelope
    markers before it is interpolated.
    """
    return message, True


async def _gate(
    definition: ToolDefinition, call: ToolCall, context: dict[str, Any]
) -> str | None:
    """Push the call through the enforcement chokepoint.

    Returns:
        str | None: A denial observation, or None when the call may proceed.

    Raises:
        ApprovalPendingError: A durable human-in-the-loop pause.
        BudgetExceededError: A per-request cap was hit (fail-closed).
    """
    from core.orchestration.autonomy import ApprovalPendingError
    from core.orchestration.enforcement import enforce_tool_invocation
    from core.orchestration.limits import BudgetExceededError

    try:
        await enforce_tool_invocation(
            context,
            definition.name,
            definition.normalized_category(),
            args=call.arguments or {},
        )
    except (ApprovalPendingError, BudgetExceededError):
        raise
    except Exception as exc:
        return gate_denial(definition.name, exc)
    return None


async def execute_tool(
    agent: Agent,
    call: ToolCall,
    *,
    context: dict[str, Any],
    run_id: str | None = None,
    step: int = 0,
) -> tuple[str, bool]:
    """Validate, gate, run and render one tool call.

    Args:
        agent: The owning agent (tools, ledger, hooks).
        call: What the model asked for.
        context: The gate context from :func:`gate_context`.
        run_id: Identifier shared by every attempt at this run; without one the
            idempotency ledger has nothing to deduplicate against.
        step: Position of the call within the run, so a loop that legitimately
            calls one tool twice with identical arguments is not collapsed into
            a single ledger entry.

    Returns:
        tuple[str, bool]: ``(observation, is_error)`` — the text the model
        reads and whether it describes a failure. ``is_error`` rides back on
        the ``tool_result`` block; a failure the model cannot see is a failure
        it will not correct.

    Raises:
        ApprovalPendingError: A durable human-in-the-loop pause.
        BudgetExceededError: A per-request cap was hit (fail-closed).
    """
    definition, early = await prepare_tool_call(agent, call, context)
    if definition is None:
        assert early is not None
        return early
    return await run_prepared_call(agent, definition, call, run_id=run_id, step=step)


async def prepare_tool_call(
    agent: Agent, call: ToolCall, context: dict[str, Any]
) -> tuple[ToolDefinition | None, tuple[str, bool] | None]:
    """Resolve and gate one call without running it.

    Separated from the execution half so a multi-tool turn can gate its calls
    strictly in order and then overlap only the approved invocations. Gate
    order is load-bearing: approval and budget refusals are fail-closed and
    abort the turn, so a tool later in the turn must not already be running
    when an earlier one is denied.

    Args:
        agent: The owning agent.
        call: What the model asked for.
        context: The gate context from :func:`gate_context`.

    Returns:
        ``(definition, None)`` when the call may run, or ``(None, outcome)``
        with the observation to return in its place.

    Raises:
        ApprovalPendingError: A durable human-in-the-loop pause.
        BudgetExceededError: A per-request cap was hit (fail-closed).
    """
    definition = agent._tools.get(call.name)
    if definition is None:
        return None, _runtime_error(
            f"Error: unknown tool {escape_untrusted_markers(call.name)!r}"
        )

    invalid = invalid_arguments_message(definition, call.arguments or {})
    if invalid is not None:
        # Before the gate on purpose: a call the model got wrong must not
        # consume an approval, a rate-limit slot, a budget entry or a ledger
        # claim for work that was never going to run.
        return None, _runtime_error(invalid)

    denial = await _gate(definition, call, context)
    if denial is not None:
        return None, _runtime_error(denial)

    return definition, None


async def run_prepared_call(
    agent: Agent,
    definition: ToolDefinition,
    call: ToolCall,
    *,
    run_id: str | None = None,
    step: int = 0,
) -> tuple[str, bool]:
    """Run an already-gated call and render its result.

    Args:
        agent: The owning agent.
        definition: The tool, as resolved by :func:`prepare_tool_call`.
        call: What the model asked for.
        run_id: Identifier shared by every attempt at this run.
        step: Position of the call within the run.

    Returns:
        tuple[str, bool]: ``(observation, is_error)``.
    """
    started = time.perf_counter()
    observation, ok = await _dispatch(agent, definition, call, run_id, step)
    await dispatch_post_hook(
        agent, definition, ok=ok, elapsed_ms=(time.perf_counter() - started) * 1000
    )
    return observation, not ok


async def _dispatch(
    agent: Agent,
    definition: ToolDefinition,
    call: ToolCall,
    run_id: str | None,
    step: int,
) -> tuple[str, bool]:
    """Run the tool (or replay it from the ledger) and render the result."""
    key = agent._ledger_key(definition, call, run_id, step)
    ledger = agent._tool_ledger
    if key is not None and ledger is not None:
        held = await ledger.begin(key, run_id=run_id or "", tool=call.name)
        if held is not None:
            return _replayed(call.name, key, held)

    try:
        from core.agent._tool_runtime import invoke_tool

        raw = await invoke_tool(agent, definition, call)
    except Exception as exc:
        logger.warning(f"agent tool {call.name} failed: {exc}")
        if key is not None and ledger is not None:
            # Record the failure so the key is re-claimable: the effect did not
            # land, and a retry of this run must be allowed to try again.
            await ledger.fail(key, str(exc))
        return (
            f"Error: tool {escape_untrusted_markers(call.name)} failed: "
            f"{escape_untrusted_markers(str(exc))}",
            False,
        )

    observation, ok = render_observation(call.name, _renderable(raw))
    if not observation:
        # A successful SkillResult with neither snapshot nor message renders to
        # "". Every earlier consumer concatenated observations into a prompt,
        # where an empty one simply vanished; this path puts it on the wire as
        # a ``tool_result`` block, and an empty content block is a 400 on some
        # providers. Runtime narration, so it stays outside the envelope.
        observation = _EMPTY_RESULT_NOTE
    if key is not None and ledger is not None:
        # The rendered observation is what is stored, so a replay returns text
        # that is already bounded, scanned and wrapped — re-wrapping it would
        # nest a second envelope around content the model already trusts as
        # data.
        await ledger.complete(key, observation)
    return observation, ok


def _replayed(tool_name: str, key: str, held: Any) -> tuple[str, bool]:
    """Turn a held ledger entry into an observation without re-running."""
    if not held.is_replayable:
        # Claimed by someone else and unresolved. Re-running an effectful call
        # on a maybe is the defect the ledger exists to prevent, so the model
        # is told rather than the tool being called again.
        in_flight = ToolCallInFlight(tool_name, key)
        logger.warning(str(in_flight))
        return gate_denial(tool_name, in_flight), False
    logger.info(f"agent tool {tool_name} replayed from ledger")
    observation = (
        held.result
        if isinstance(held.result, str)
        else json.dumps(held.result, default=str)
    )
    # A row written before the empty-result guard existed (or by another
    # writer) can still be empty, and this is the one remaining route to an
    # empty ``tool_result`` content block on the wire.
    observation = observation or _EMPTY_RESULT_NOTE
    # A stored observation is verbatim what the model saw last time, envelope
    # included; the ``Error`` prefix there is runtime-authored (tool text is
    # sealed inside the envelope and cannot forge one), so it still reads as
    # the failure flag it was.
    return observation, not observation.startswith("Error")
