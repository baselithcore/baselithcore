"""Typed developer-facing Agent API.

The one-import entry point for building agents on BaselithCore, in the style
popularized by typed-agent frameworks: declare the model, an optional Pydantic
``output_type``, plain-Python tools, and call :meth:`Agent.run`.

    from pydantic import BaseModel
    from core.agent import Agent

    class CityInfo(BaseModel):
        city: str
        population: int

    async def lookup_population(city: str) -> str:
        \"\"\"Look up a city's population.\"\"\"
        ...

    agent = Agent(output_type=CityInfo, tools=[lookup_population])
    result = await agent.run("Tell me about Rome")
    result.output  # -> CityInfo, validated (with automatic retry on failure)

Everything runs on the existing runtime: ``LLMService`` (provider abstraction,
caching, cost accounting, fallback chain, routing), the structured
tool-calling path, and the ambient ``LoopBudget`` — an ``Agent.run`` inside an
orchestrated request charges that request's budget like any other LLM call.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel, ValidationError

from core.observability.logging import get_logger
from core.reasoning.react import ToolDefinition
from core.reasoning.react_native import infer_tool_parameters
from core.services.llm.tool_calling import LLMToolSpec, ResponseFormat, ToolCall

logger = get_logger(__name__)

OutputT = TypeVar("OutputT")

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class AgentOutputValidationError(RuntimeError):
    """The model never produced output matching ``output_type``."""

    def __init__(self, message: str, last_error: Exception | None = None) -> None:
        super().__init__(message)
        self.last_error = last_error


@dataclass(frozen=True)
class AgentResult(Generic[OutputT]):
    """Outcome of one :meth:`Agent.run` call.

    Attributes:
        output: The validated ``output_type`` instance, or the plain response
            text when no ``output_type`` was declared.
        text: The final raw assistant text.
        tool_calls_made: Names of tools executed, in call order.
        iterations: LLM round-trips performed (tool loops + validation
            retries).
    """

    output: OutputT
    text: str
    tool_calls_made: list[str] = field(default_factory=list)
    iterations: int = 0


class Agent(Generic[OutputT]):
    """A typed agent: model + system prompt + tools + validated output.

    Args:
        model: Optional model override (deployment default when None).
        output_type: Optional Pydantic model the final answer must satisfy.
            Requested natively via the provider's structured-output API and
            validated locally; validation failures are fed back to the model
            and retried up to ``max_retries`` times.
        system_prompt: Optional system prompt.
        tools: Plain callables (sync or async — the JSON schema is inferred
            from type hints and the docstring) or explicit ``ToolDefinition``s.
        max_retries: Validation-failure retries for ``output_type``.
        max_iterations: Hard cap on LLM round-trips (tool loop + retries).
        task_category: Optional cost-aware routing hint (TaskCategory value).
        llm_service: Injected service (tests); defaults to the shared
            context-aware :func:`core.services.llm.get_llm_service`.
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        output_type: type[OutputT] | None = None,
        system_prompt: str | None = None,
        tools: Sequence[Callable[..., Any] | ToolDefinition] = (),
        max_retries: int = 2,
        max_iterations: int = 6,
        task_category: str | None = None,
        llm_service: Any | None = None,
    ) -> None:
        self.model = model
        self.output_type = output_type
        self.system_prompt = system_prompt
        self.max_retries = max_retries
        self.max_iterations = max_iterations
        self.task_category = task_category
        self._llm_service = llm_service
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools:
            definition = (
                tool
                if isinstance(tool, ToolDefinition)
                else ToolDefinition(
                    name=tool.__name__,
                    fn=tool,
                    description=inspect.getdoc(tool) or tool.__name__,
                )
            )
            self._tools[definition.name] = definition

    # -- internals ---------------------------------------------------------

    def _service(self) -> Any:
        if self._llm_service is not None:
            return self._llm_service
        from core.services.llm import get_llm_service

        return get_llm_service()

    def _tool_specs(self) -> list[LLMToolSpec] | None:
        if not self._tools:
            return None
        return [
            LLMToolSpec(
                name=t.name,
                description=t.description,
                parameters=getattr(t, "parameters", None) or infer_tool_parameters(t),
            )
            for t in self._tools.values()
        ]

    def _response_format(self) -> ResponseFormat | None:
        if self.output_type is None or not issubclass(self.output_type, BaseModel):
            return None
        return ResponseFormat(
            schema=self.output_type.model_json_schema(),
            name=self.output_type.__name__,
            strict=True,
        )

    async def _execute_tool(self, call: ToolCall) -> str:
        definition = self._tools.get(call.name)
        if definition is None:
            return f"Error: unknown tool {call.name!r}"
        try:
            result = definition.fn(**(call.arguments or {}))
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.warning(f"agent tool {call.name} failed: {exc}")
            return f"Error: tool {call.name} failed: {exc}"
        return result if isinstance(result, str) else json.dumps(result, default=str)

    def _parse_output(self, text: str) -> OutputT:
        assert self.output_type is not None
        cleaned = _FENCE_RE.sub("", text.strip()).strip()
        model_cls = cast("type[BaseModel]", self.output_type)
        return cast(OutputT, model_cls.model_validate(json.loads(cleaned)))

    # -- public API --------------------------------------------------------

    async def run(self, prompt: str) -> AgentResult[OutputT]:
        """Run the agent to completion and return the validated result.

        Drives the tool loop until the model answers without tool calls, then
        (when ``output_type`` is set) validates the answer, feeding validation
        errors back for up to ``max_retries`` correction rounds.

        Raises:
            AgentOutputValidationError: ``output_type`` never satisfied.
            RuntimeError: ``max_iterations`` exhausted before a final answer.
        """
        service = self._service()
        specs = self._tool_specs()
        response_format = self._response_format()
        current = prompt
        tool_calls_made: list[str] = []
        # Accumulated across rounds, not per round: a loop that rebuilt the
        # prompt from the latest round alone would drop everything the earlier
        # tool calls established, so the model keeps re-requesting work whose
        # answer it was already given — and the loop runs until
        # ``max_iterations`` instead of converging.
        tool_blocks: list[str] = []
        retries_left = self.max_retries
        last_error: Exception | None = None

        for iteration in range(1, self.max_iterations + 1):
            result = await service.generate(
                current,
                model=self.model,
                tools=specs,
                response_format=response_format,
                system_prompt=self.system_prompt,
                task_category=self.task_category,
            )
            if result.tool_calls:
                for call in result.tool_calls:
                    output = await self._execute_tool(call)
                    tool_calls_made.append(call.name)
                    tool_blocks.append(f"[{call.name}] -> {output}")
                current = (
                    f"{prompt}\n\nTool results so far:\n"
                    + "\n".join(tool_blocks)
                    + "\n\nContinue. Use the tool results above; when you have "
                    "enough information, answer without calling more tools."
                )
                continue

            text = result.text or ""
            if self.output_type is None:
                return AgentResult(
                    output=text,  # type: ignore[arg-type]
                    text=text,
                    tool_calls_made=tool_calls_made,
                    iterations=iteration,
                )
            try:
                parsed = self._parse_output(text)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                if retries_left <= 0:
                    raise AgentOutputValidationError(
                        f"output failed {self.output_type.__name__} validation "
                        f"after {self.max_retries} retries: {exc}",
                        last_error=exc,
                    ) from exc
                retries_left -= 1
                current = (
                    f"{prompt}\n\nYour previous answer was:\n{text}\n\n"
                    f"It failed validation against the required schema: {exc}\n"
                    "Reply again with ONLY a JSON object matching the schema."
                )
                continue
            return AgentResult(
                output=parsed,
                text=text,
                tool_calls_made=tool_calls_made,
                iterations=iteration,
            )

        raise RuntimeError(
            f"Agent.run exceeded max_iterations={self.max_iterations} "
            f"(last validation error: {last_error})"
        )

    async def run_stream(self, prompt: str) -> AsyncIterator[str]:
        """Stream the plain-text response token by token.

        Streaming is text-only: combining it with ``output_type`` or tools is
        rejected (validated/structured answers need the complete response).
        """
        if self.output_type is not None:
            raise ValueError("run_stream does not support output_type")
        if self._tools:
            raise ValueError("run_stream does not support tools")
        service = self._service()
        async for chunk in service.generate_response_stream(
            prompt,
            model=self.model,
            system_prompt=self.system_prompt,
        ):
            yield chunk
