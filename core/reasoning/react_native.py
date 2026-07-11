"""Native tool-calling execution path for the ReAct agent.

The legacy :class:`~core.reasoning.react.ReActAgent` loop asks the model to
emit ``Action: tool(args)`` text and regex-parses it back. This module drives
the same Thought/Action/Observation loop over the structured LLM API instead:
tools are described as JSON-Schema specs, the model replies with parsed
:class:`~core.services.llm.tool_calling.ToolCall` objects
(``LLMResult.tool_calls``), and arguments arrive as typed keyword dicts —
no text parsing, multi-tool turns supported.

Kept out of ``react.py`` to respect the module size cap. The agent's public
contract is unchanged: same :class:`~core.reasoning.react.ReActResult`, same
trace shape, same guarded tool execution (timeout / transient retry / output
truncation).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.reasoning.react import ReActAgent, ReActResult, ToolDefinition
    from core.services.llm.tool_calling import LLMToolSpec

logger = get_logger(__name__)

_NATIVE_SYSTEM_TEMPLATE = """\
You are an intelligent agent that answers questions by reasoning step by step \
and using the available tools.

Rules:
- Call tools through the tool-calling interface whenever you need information \
or actions; never describe a call in prose.
- Use at most {max_iterations} tool-calling turns in total.
- If you cannot find the answer, say so honestly — never fabricate.
- When you have enough information, reply with your complete, definitive \
answer without calling any tool.
"""

_CONTINUE_INSTRUCTION = (
    "Continue. Use the tool results above; when you have enough information, "
    "answer without calling more tools."
)

# Python annotation -> JSON-Schema type. String forms cover callables defined
# under ``from __future__ import annotations`` (signature stores the literal).
_JSON_TYPES: dict[Any, str] = {
    str: "string",
    "str": "string",
    int: "integer",
    "int": "integer",
    float: "number",
    "float": "number",
    bool: "boolean",
    "bool": "boolean",
    dict: "object",
    "dict": "object",
    list: "array",
    "list": "array",
}


def infer_tool_parameters(tool: ToolDefinition) -> dict[str, Any]:
    """Return the JSON-Schema object describing *tool*'s arguments.

    An explicit ``tool.parameters`` wins. Otherwise the schema is inferred
    from the callable's signature: each named parameter becomes a property
    (annotation mapped to a JSON type, ``string`` when unknown) and
    parameters without a default are required. Uninspectable callables get
    the permissive ``{"type": "object"}``.
    """
    if tool.parameters is not None:
        return tool.parameters

    try:
        signature = inspect.signature(tool.fn)
    except (TypeError, ValueError):
        return {"type": "object"}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in signature.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        properties[name] = {"type": _JSON_TYPES.get(param.annotation, "string")}
        if param.default is param.empty:
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def build_tool_specs(tools: Iterable[ToolDefinition]) -> list[LLMToolSpec]:
    """Adapt :class:`ToolDefinition` objects to native ``LLMToolSpec``s."""
    from core.services.llm.tool_calling import LLMToolSpec

    return [
        LLMToolSpec(
            name=tool.name,
            description=tool.description,
            parameters=infer_tool_parameters(tool),
        )
        for tool in tools
    ]


def resolve_native_mode(agent: ReActAgent) -> bool:
    """Decide whether *agent* should run the native tool-calling loop.

    An explicit ``native_tools`` flag wins (``True`` still requires a service
    exposing the structured ``generate`` API — otherwise the text loop runs
    with a warning). Auto (``None``) mirrors the routing inside
    ``LLMService.generate``: native only when the service config enables
    native tools *and* the active provider supports them, so auto mode never
    silently lands on the weaker prompt-coercion fallback.
    """
    llm = agent._get_llm_service()
    if llm is None or not callable(getattr(llm, "generate", None)):
        if agent._native_tools:
            logger.warning(
                "native_tools=True but the LLM service exposes no structured "
                "generate(); falling back to the text-parsing loop."
            )
        return False

    if agent._native_tools is not None:
        return agent._native_tools

    # Strict identity on the bool flags: auto mode must only flip the loop
    # when the flags are literally True — truthy stand-ins (e.g. mock or
    # duck-typed service doubles) must not silently change the execution path.
    config = getattr(llm, "config", None)
    if getattr(config, "enable_native_tools", False) is not True:
        return False
    try:
        provider = getattr(llm, "provider", None)
    except Exception:  # provider construction may fail (e.g. missing key)
        return False
    return getattr(provider, "supports_native_tools", False) is True


def _build_system_prompt(agent: ReActAgent) -> str:
    prompt = _NATIVE_SYSTEM_TEMPLATE.format(max_iterations=agent.max_iterations)
    if agent._system_prompt_extra:
        prompt += f"\n\n{agent._system_prompt_extra}"
    return prompt


def _last_observation(trace: list[Any]) -> str:
    from core.reasoning.react import StepType

    return next(
        (s.content for s in reversed(trace) if s.step_type is StepType.OBSERVATION),
        "Unable to determine a final answer within the iteration budget.",
    )


async def run_native_loop(agent: ReActAgent, query: str) -> ReActResult:
    """Execute the ReAct loop for *query* over the native tool-calling API.

    Same contract as :meth:`ReActAgent.run`: bounded by ``max_iterations``,
    returns a :class:`ReActResult` with the familiar
    Thought/Action/Observation trace. Tool calls within one model turn are
    executed sequentially in emission order (observations may feed the next
    reasoning turn), each through the agent's guarded executor.
    """
    from core.reasoning.react import ReActResult, StepType, TraceStep

    trace: list[TraceStep] = []
    llm = agent._get_llm_service()
    if llm is None:
        return ReActResult(
            final_answer="LLM service unavailable.",
            trace=trace,
            iterations_used=0,
            hit_limit=False,
        )

    specs = build_tool_specs(agent._tools.values())
    system_prompt = _build_system_prompt(agent)
    transcript: list[str] = [f"User: {query}"]

    for iteration in range(1, agent.max_iterations + 1):
        # Deterministic compaction bounds prompt growth (cost/latency) on
        # long runs; the newest entries always stay intact.
        from core.reasoning.history import compact_history

        transcript = compact_history(transcript)
        prompt = "\n\n".join(transcript)
        if iteration > 1:
            prompt = f"{prompt}\n\nUser: {_CONTINUE_INSTRUCTION}"
        try:
            result = await llm.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                tools=specs,
            )
        except Exception as exc:
            logger.error("ReAct native LLM call failed: %s", exc)
            return ReActResult(
                final_answer="An error occurred while processing your request.",
                trace=trace,
                iterations_used=iteration,
                hit_limit=False,
            )

        text = (result.text or "").strip()
        if text:
            trace.append(TraceStep(StepType.THOUGHT, iteration, text))

        if not result.tool_calls:
            answer = text or _last_observation(trace)
            trace.append(TraceStep(StepType.FINAL_ANSWER, iteration, answer))
            return ReActResult(
                final_answer=answer,
                trace=trace,
                iterations_used=iteration,
                hit_limit=False,
            )

        if text:
            transcript.append(f"Assistant: {text}")
        for call in result.tool_calls:
            args_repr = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
            trace.append(
                TraceStep(
                    StepType.ACTION,
                    iteration,
                    f"{call.name}({args_repr})",
                    tool_name=call.name,
                    tool_args=args_repr,
                )
            )
            observation = await agent._execute_tool_call(call.name, call.arguments)
            trace.append(TraceStep(StepType.OBSERVATION, iteration, observation))
            transcript.append(
                f"Assistant: [tool call {call.id}] {call.name}({args_repr})"
            )
            transcript.append(f"Tool result [{call.id}]: {observation}")

    logger.warning(
        "ReAct (native) hit max_iterations=%d without a final answer.",
        agent.max_iterations,
    )
    return ReActResult(
        final_answer=_last_observation(trace),
        trace=trace,
        iterations_used=agent.max_iterations,
        hit_limit=True,
    )


__all__ = [
    "build_tool_specs",
    "infer_tool_parameters",
    "resolve_native_mode",
    "run_native_loop",
]
