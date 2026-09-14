"""Everything the ReAct loop does *around* a tool call, except running it.

Four concerns, deliberately kept out of :mod:`core.reasoning.react_tools` so
the mixin stays readable (and the module stays under the size cap):

1. **Gating** — build an orchestration context from the agent's primitives and
   push the call through :func:`core.orchestration.enforcement.enforce_tool_invocation`,
   the single chokepoint. The loop used to re-implement contract → autonomy →
   budget by hand, so every control added at the chokepoint (plugin capability
   check, tool rate limit, pre-hooks, audit trail) applied to orchestrated
   handlers and silently skipped the path that actually calls tools.
2. **Argument validation** — the model writes the arguments. Splatting them
   into a callable turns a hallucinated name or a string-where-int-goes into an
   opaque ``TypeError``; validating against the tool's JSON Schema turns it
   into a message the model can act on.
3. **Idempotency** — a ledger claim for every non-``read_only`` call, so a
   replayed effect is returned rather than repeated.
4. **Rendering** — a ``SkillResult`` becomes its ``snapshot`` (not a Pydantic
   repr), and every tool-controlled observation leaves inside the untrusted
   envelope.
"""

from __future__ import annotations

import inspect
import uuid
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.orchestration.idempotency import (
    InMemoryToolLedger,
    ToolCallInFlight,
    derive_idempotency_key,
    requires_idempotency,
)
from core.orchestration.tool_output import (
    escape_untrusted_markers,
    sanitize_tool_output,
    truncate_tool_output,
    wrap_untrusted,
)

if TYPE_CHECKING:
    from core.reasoning.react_types import ToolDefinition

logger = get_logger(__name__)

__all__ = [
    "ToolArgumentError",
    "build_gate_context",
    "claim_ledger_entry",
    "dispatch_post_hook",
    "gate_denial",
    "invalid_arguments_message",
    "new_ledger",
    "new_run_id",
    "note_tool_outcome",
    "render_observation",
    "validate_arguments",
]


class ToolArgumentError(ValueError):
    """The model's arguments do not satisfy the tool's declared schema."""


def _ambient_tenant() -> str | None:
    """The current tenant, or None when unset/unavailable.

    Strict tenant isolation raises when no tenant is bound; a ReAct agent can
    legitimately run outside a request (CLI, eval harness), and the tenant is
    only used to scope the rate limiter and the audit record.
    """
    try:
        from core.context import get_current_tenant_id

        return get_current_tenant_id()
    except Exception:  # silent-ok: an unbound tenant is a normal CLI/eval case
        return None


def build_gate_context(agent: Any) -> dict[str, Any]:
    """Assemble the orchestration context the enforcement chokepoint reads.

    Only the primitives the agent actually holds are placed on it; each control
    at the chokepoint is a no-op when its key is absent, so an agent wired with
    nothing still passes through the shared plugin-capability, rate-limit,
    pre-hook and audit steps.
    """
    context: dict[str, Any] = {"tenant_id": _ambient_tenant()}
    if agent._contract_validator is not None:
        context["contract_validator"] = agent._contract_validator
    if agent._autonomy_policy is not None:
        context["autonomy_policy"] = agent._autonomy_policy
    if agent._human_intervention is not None:
        context["human_intervention"] = agent._human_intervention
    if agent._checkpoint is not None:
        context["checkpoint"] = agent._checkpoint
    budget = agent._active_budget()
    if budget is not None:
        context["loop_budget"] = budget
    hooks = getattr(agent, "_tool_hooks", None)
    if hooks is not None:
        context["tool_hooks"] = hooks
    return context


def gate_denial(tool_name: str, exc: Exception) -> str:
    """Render a refused invocation as an observation the model can react to.

    Both interpolated fragments are scrubbed of envelope markers: this string
    lands *outside* the untrusted envelope (it is the runtime speaking), and a
    tool name the model invented or an exception carrying remote text must not
    be able to forge a boundary there.
    """
    logger.warning("ReAct tool '%s' blocked: %s", tool_name, exc)
    return (
        f"Error executing '{escape_untrusted_markers(tool_name)}': "
        f"{escape_untrusted_markers(str(exc))}"
    )


def _accepts_extra_kwargs(fn: Any) -> bool:
    """Whether ``fn`` really would swallow keys its signature does not name."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):  # builtins, C callables, mocks
        return True
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
    )


def _strictened(tool: ToolDefinition, schema: dict[str, Any]) -> dict[str, Any]:
    """Close an *inferred* schema to extra keys.

    An inferred schema mirrors the callable's signature exactly, so a key it
    does not name is one the call will reject anyway — as a bare ``TypeError``
    raised *after* the invocation consumed an approval, a rate-limit slot and a
    tool-call budget entry. Refusing it up front turns a hallucinated argument
    into a message the model can correct on its next turn.

    Two schemas are deliberately left alone: one the tool author declared
    (``parameters`` is their contract, and tightening it would change their
    meaning) and one whose callable takes ``**kwargs`` (which genuinely does
    accept extra keys).
    """
    if tool.parameters is not None or _accepts_extra_kwargs(tool.fn):
        return schema
    if "additionalProperties" in schema:
        return schema
    return {**schema, "additionalProperties": False}


def validate_arguments(tool: ToolDefinition, arguments: dict[str, Any]) -> None:
    """Check model-supplied ``arguments`` against *tool*'s JSON Schema.

    Args:
        tool: The tool about to be invoked.
        arguments: Keyword arguments as produced by the model.

    Raises:
        ToolArgumentError: The arguments violate the schema. The message names
            the offending field and what was expected, so the model can fix
            the call on its next turn instead of guessing at a ``TypeError``.
    """
    schema = tool.json_schema()
    # A schema with no ``properties`` — a permissive ``{"type": "object"}`` from
    # an uninspectable callable, or a ``**kwargs``-only tool — describes nothing
    # to check against, so nothing here is validated. That is not a gap being
    # tolerated: there is no declared contract to hold the model to, and
    # inventing one would reject calls the tool would have accepted.
    if not isinstance(schema, dict) or not schema.get("properties"):
        return
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a hard dependency
        logger.debug("jsonschema unavailable; skipping tool argument validation")
        return
    try:
        jsonschema.validate(instance=arguments, schema=_strictened(tool, schema))
    except jsonschema.ValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path) or "<arguments>"
        raise ToolArgumentError(f"{path}: {exc.message}") from exc
    except jsonschema.SchemaError as exc:  # pragma: no cover - malformed tool
        logger.warning("Tool '%s' declares an invalid schema: %s", tool.name, exc)


def new_ledger() -> InMemoryToolLedger:
    """A fresh in-process ledger (the default when the host wires none)."""
    return InMemoryToolLedger()


def new_run_id() -> str:
    """Fallback run id for ledger keys outside a checkpointed run."""
    return uuid.uuid4().hex


async def claim_ledger_entry(
    ledger: Any,
    run_id: str,
    step: int,
    tool: ToolDefinition,
    args: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Claim the idempotency key for one effectful call.

    Args:
        ledger: The :class:`~core.orchestration.idempotency.ToolLedger`.
        run_id: Run the call belongs to.
        step: Position of the call within the run.
        tool: The tool about to run.
        args: Its arguments (part of the derived key; never stored raw).

    Returns:
        ``(key, replayed_observation)``. A non-None observation means the call
        must NOT run: either its outcome was already recorded (return it) or
        another worker holds the claim right now (an error observation). A
        ``None`` key means the ledger is not in play for this category.
    """
    if not requires_idempotency(tool.category):
        return None, None
    key = derive_idempotency_key(run_id, step, tool.name, args)
    try:
        held = await ledger.begin(key, run_id=run_id, tool=tool.name)
    except Exception as exc:  # ledger trouble must not block the loop
        logger.warning("tool_ledger_begin_failed tool=%s error=%s", tool.name, exc)
        return None, None
    if held is None:
        return key, None
    if held.is_replayable:
        logger.info("tool_ledger_replay tool=%s key=%s", tool.name, key[:12])
        return key, str(held.result)
    return key, gate_denial(tool.name, ToolCallInFlight(tool.name, key))


def render_observation(tool_name: str, result: Any) -> tuple[str, bool]:
    """Turn a raw tool return value into the observation the model reads.

    A :class:`~core.plugins.result.SkillResult` is unpacked rather than
    stringified: its ``snapshot`` is the LLM-facing, bounded view of the
    payload, and its ``success`` flag — not the shape of the text — decides
    whether this counted as a failure. Stringifying it fed the model a Pydantic
    repr *and* told the failure-streak guard that a failed skill had succeeded.

    Everything a tool produced is then bounded, scanned and wrapped in the
    untrusted envelope. Runtime-authored prefixes stay outside it: they are the
    loop's own narration, and teaching the model to distrust those would be
    teaching it to distrust the runtime.

    Args:
        tool_name: Tool that produced ``result`` (recorded in the envelope).
        result: Whatever the tool returned.

    Returns:
        ``(observation, ok)`` — the text to show the model, and whether the
        call is to be counted as a success.
    """
    from core.plugins.result import SkillResult

    if isinstance(result, SkillResult):
        body = (
            result.snapshot if result.snapshot is not None else (result.message or "")
        )
        wrapped = _wrap(tool_name, body) if body else ""
        if result.success:
            return wrapped, True
        detail = result.message or result.error_code or "skill failed"
        # ``detail`` is the tool's own message and this prefix sits outside the
        # envelope, so its markers are neutralised before interpolation.
        prefix = (
            f"Error executing '{escape_untrusted_markers(tool_name)}': "
            f"{escape_untrusted_markers(detail)}"
        )
        return (f"{prefix}\n{wrapped}" if wrapped else prefix), False
    return _wrap(tool_name, str(result)), True


def _wrap(tool_name: str, text: str) -> str:
    """Bound, scan and envelope one piece of tool-controlled text."""
    return wrap_untrusted(
        sanitize_tool_output(truncate_tool_output(text), source=tool_name),
        source=tool_name,
    )


async def dispatch_post_hook(
    agent: Any, tool: ToolDefinition, *, ok: bool, elapsed_ms: float
) -> None:
    """Fire ``post`` hooks for a finished call.

    The hook bus shipped a ``dispatch_post`` that core never called: operators
    could register "lint after editing" or "audit every write" and watch them
    never fire. Post hooks are observers by contract — the registry swallows
    their failures — so this can never change a tool's outcome.
    """
    from core.orchestration.hooks import ToolHookEvent, get_tool_hook_registry

    hooks = getattr(agent, "_tool_hooks", None) or get_tool_hook_registry()
    try:
        await hooks.dispatch_post(
            ToolHookEvent(
                tool_name=tool.name,
                category=tool.normalized_category(),
                phase="post",
                metadata={"ok": ok, "elapsed_ms": round(elapsed_ms, 3)},
            )
        )
    except Exception:  # pragma: no cover - observers never break a tool
        logger.debug("tool_post_hook_dispatch_failed", exc_info=True)


def invalid_arguments_message(
    tool: ToolDefinition, kwargs: dict[str, Any]
) -> str | None:
    """The observation to return for a malformed call, or None when it is fine.

    Called *before* the gate on purpose: a call the model got wrong must not
    consume an approval, a rate-limit slot, a tool-call budget entry or a
    ledger claim for work that was never going to run.
    """
    try:
        validate_arguments(tool, kwargs)
    except ToolArgumentError as exc:
        logger.info("ReAct tool '%s' called with invalid arguments: %s", tool.name, exc)
        return (
            f"Error executing '{escape_untrusted_markers(tool.name)}': "
            f"invalid arguments — {escape_untrusted_markers(str(exc))}. "
            f"Expected schema: {tool.json_schema()}"
        )
    return None


def note_tool_outcome(agent: Any, observation: str) -> str | None:
    """Track the consecutive-failure streak; return an escalation message
    when the configured cap is crossed, else None.

    Failed observations are the error strings produced by the guarded
    executor (``Error ...``); any success resets the streak. Escalating
    early keeps a broken tool from burning the whole iteration budget.

    The ``Error`` prefix is now a *runtime-generated* marker and only that:
    a tool's own text always leaves this module sealed inside the untrusted
    envelope (``<untrusted_tool_output …>``), so a tool that returns the
    literal string ``Error: ...`` can no longer spoof a failure — or, more
    interestingly, keep the streak at zero by never producing one. The one
    exception is deliberate: a failed ``SkillResult`` gets an ``Error
    executing '<tool>':`` prefix written *here*, because ``success=False``
    is the tool declaring its own failure through the contract rather than
    through the shape of its text.
    """
    cap = agent._max_consecutive_tool_failures
    if cap is None and agent._stall_guard is None:
        return None
    if observation.startswith("Error"):
        agent._failure_streak += 1
    else:
        agent._failure_streak = 0
        return None

    # Futility check: the streak counts *how many* failures; the stall
    # guard counts how many times the *same* failure came back. A tool
    # that keeps returning a different error each time is still making
    # the loop pay for nothing.
    if agent._stall_guard is not None:
        verdict = agent._stall_guard.record(observation)
        if verdict.stalled:
            logger.warning(
                "ReAct: %s — escalating instead of continuing the loop.",
                verdict.reason,
            )
            return (
                f"Stopping: {verdict.reason} (last: {observation}). "
                "Please review the tool configuration or retry later."
            )

    if cap is None or agent._failure_streak < cap:
        return None
    logger.warning(
        "ReAct: %d consecutive tool failures — escalating instead of "
        "continuing the loop.",
        agent._failure_streak,
    )
    return (
        f"Stopping: tools failed {agent._failure_streak} consecutive times "
        f"(last: {observation}). Please review the tool configuration or "
        "retry later."
    )
