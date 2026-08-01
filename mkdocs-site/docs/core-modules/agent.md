---
title: Agent API (typed)
description: The one-import typed Agent — Pydantic-validated output, plain-Python tools, automatic retry
---

`core/agent/` is the **developer-facing entry point** for building agents on
BaselithCore: declare a model, an optional Pydantic `output_type`, and
plain-Python tools — then `await agent.run(...)` and get a validated result.

```python
from pydantic import BaseModel
from core.agent import Agent

class CityInfo(BaseModel):
    city: str
    population: int

async def lookup_population(city: str) -> str:
    """Look up a city's population."""
    ...

agent = Agent(
    output_type=CityInfo,
    tools=[lookup_population],
    system_prompt="You are a precise geography assistant.",
)

result = await agent.run("Tell me about Rome")
result.output            # CityInfo(city="Rome", population=2870000) — validated
result.tool_calls_made   # ["lookup_population"]
result.iterations        # LLM round-trips used
```

## What you get

- **Typed, validated output** — `output_type` is requested natively via the
  provider's structured-output API (`ResponseFormat`, JSON-Schema) *and*
  validated locally with Pydantic. A validation failure is fed back to the
  model with the error message and retried up to `max_retries` times;
  exhausted retries raise `AgentOutputValidationError`.
- **Plain-Python tools** — pass sync or async callables; the JSON schema is
  inferred from type hints and the docstring (explicit
  `ToolDefinition`s are accepted too). The tool loop runs until the model
  answers without tool calls, bounded by `max_iterations`.
- **Streaming** — `agent.run_stream(prompt)` yields text chunks
  (text-only: `output_type`/tools are rejected on the stream path).
- **The whole runtime underneath** — calls go through `LLMService`, so
  provider abstraction, caching, cost accounting, the cross-provider
  fallback chain, cost-aware routing (`task_category=`), and the ambient
  `LoopBudget` all apply. An `Agent.run` inside an orchestrated request
  charges that request's budget like any other LLM call.

## Constructor

| Parameter | Default | Meaning |
|---|---|---|
| `model` | deployment default | Per-agent model override (pinned policies still win) |
| `output_type` | `None` (plain text) | Pydantic model the final answer must satisfy |
| `system_prompt` | `None` | System prompt |
| `tools` | `()` | Callables or `ToolDefinition`s |
| `max_retries` | `2` | Validation-failure correction rounds |
| `max_iterations` | `6` | Hard cap on LLM round-trips (tools + retries) |
| `task_category` | `None` | Cost-aware routing hint (`TaskCategory` value) |
| `llm_service` | shared service | Injection seam for tests |

## Relationship to the orchestrator

`Agent` is the *library* surface — embed it in scripts, plugins, or your own
services. The `Orchestrator` remains the *platform* surface (intent routing,
handlers, checkpointing, guard pipeline). They compose: an orchestrator
handler can build and run an `Agent` internally, inheriting the request's
budget and guardrails.
