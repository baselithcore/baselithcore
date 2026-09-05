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
  answers without tool calls, bounded by `max_iterations`. Tool results
  **accumulate across rounds** — every round's prompt carries the outputs of
  all previous calls, not just the last one, so the model never re-requests
  work it has already been given and multi-tool tasks converge instead of
  running to the `max_iterations` cap.
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

## Multi-agent crews (`Crew` + `Task`)

The declarative collaborative counterpart — a crew in ten lines:

```python
from core.agent import Agent, Crew, Task

researcher = Agent(system_prompt="You are a meticulous researcher.")
writer = Agent(system_prompt="You write crisp executive summaries.")

crew = Crew(
    agents=[researcher, writer],
    tasks=[
        Task("Research {topic} and list the key facts.", agent=researcher),
        Task("Write a summary from the research.", agent=writer),
    ],
)
result = await crew.run(inputs={"topic": "vector databases"})
result.final          # the last task's output
result.task_results   # per-task: name, output, text, agent_index,
                      #           latency_ms, cost_usd, review
```

- **Processes** — `process="sequential"` (default) threads each task's output
  into the next task's prompt as context; `process="parallel"` runs
  independent tasks concurrently with no cross-task context;
  `process="hierarchical"` adds a manager agent (below).
- **Templating** — `{placeholders}` in task descriptions are filled from
  `run(inputs=...)`; unknown placeholders are left intact. An optional
  `expected_output` per task is appended to its prompt.
- **Assignment** — every task names its `agent`; a crew with exactly one
  agent auto-assigns.
- **Everything through `Agent.run`** — tools, `output_type` validation, cost
  accounting and the ambient `LoopBudget` apply per task unchanged.

For auction-based allocation, capability matching, and structured handoffs,
use the platform surface in [`core/swarm`](swarm.md) instead — `Crew` is the
deliberate low-ceremony subset.

### Hierarchical process (manager-led)

`Crew(process="hierarchical", manager=Agent(...))` — `manager` is required
(and only used) for this process; its absence raises `ValueError` at
construction. Each task runs a bounded delegate → execute → review cycle
(`core/agent/crew_hierarchical.py`):

1. **Delegate** — the manager writes a short delegation brief from the task
   prompt; the brief is appended to the worker's prompt.
2. **Execute** — the assigned worker agent runs the task.
3. **Review** — the manager returns a strict-JSON verdict (reasoning first):
   `APPROVED` or `REVISE` with feedback.
4. **Revise (bounded)** — on `REVISE` the task re-runs **exactly once** with
   the feedback appended; the second output is accepted regardless and the
   task result is flagged `review="revised"`. There are no review loops.

Any manager LLM failure (brief, review call, or malformed review JSON)
**fails open**: the worker output is accepted as `approved` and a warning is
logged — coordination is never allowed to block delivery. Tasks still run in
order, with each accepted output threading into the next task's context as in
the sequential process.

```python
from core.agent import Agent, Crew, Task

manager = Agent(system_prompt="You are an exacting engineering manager.")
crew = Crew(
    agents=[researcher, writer],
    tasks=[
        Task("Research {topic} and list the key facts.", agent=researcher),
        Task("Write a summary from the research.", agent=writer),
    ],
    process="hierarchical",
    manager=manager,
)
result = await crew.run(inputs={"topic": "vector databases"})
result.task_results[0].review   # "approved" | "revised"
```

### Coordination tax (latency & cost accounting)

Every `TaskResult` now carries `latency_ms` (wall-clock milliseconds for the
whole task cycle — in hierarchical mode this **includes** the manager's
delegation and review turns, the coordination tax) and `cost_usd`. Cost comes
from an injected estimator, `Crew(..., cost_fn=...)` with signature
`cost_fn(task, output) -> float` (USD), applied to each task's accepted
output; without one every task costs `0.0` — latency is always measured.

`CrewResult` aggregates: `total_latency_ms`, `total_cost_usd`, and
`breakdown()`, which maps each executing agent's index in `Crew.agents`
(`-1` for off-roster agents) to a frozen `AgentUsage` of `latency_ms`,
`cost_usd`, `task_count`.

```python
result.total_latency_ms          # sum over tasks
result.total_cost_usd            # sum over tasks (0.0 without cost_fn)
for agent_index, usage in result.breakdown().items():
    print(agent_index, usage.latency_ms, usage.cost_usd, usage.task_count)
```

`AgentUsage`, `CostFn`, `ReviewDecision`, and `ReviewVerdict` are exported
from `core.agent` alongside the existing crew types.

## Group chat (`GroupChat` + speaker selection)

The collaboration topologies above are structured — `Crew` is a task DAG, the
[swarm](swarm.md) is a task market. `GroupChat`
(`core/agent/group_chat.py`) is the emergent third shape: participants share
one growing transcript and a *speaker selector* decides who talks next, so
coordination arises from the conversation itself rather than a pre-planned
graph.

```python
from core.agent import Agent, ChatMessage, GroupChat, LLMManagerSelector

class AgentParticipant:
    """Adapt an Agent to the Participant protocol."""

    def __init__(self, name: str, agent: Agent, capabilities: list[str]) -> None:
        self.name = name
        self.capabilities = capabilities
        self._agent = agent

    async def respond(self, topic: str, transcript: list[ChatMessage]) -> str:
        tail = "\n".join(f"{m.speaker}: {m.content}" for m in transcript[-6:])
        result = await self._agent.run(f"Topic: {topic}\n\n{tail}")
        return result.text

chat = GroupChat(
    participants=[
        AgentParticipant("critic", critic_agent, ["review", "risks"]),
        AgentParticipant("builder", builder_agent, ["code", "design"]),
    ],
    selector=LLMManagerSelector(llm_service),
    max_rounds=8,
    terminate=lambda transcript: "CONSENSUS" in transcript[-1].content,
)
result = await chat.run("Should we shard the vector store?")
result.transcript       # list[ChatMessage(speaker, content)]
result.rounds           # utterances produced
result.terminated_by    # "max_rounds" | "predicate" | "budget"
```

A **`Participant`** is any object with `name: str`, `capabilities: list[str]`
and `async respond(topic, transcript) -> str` (a `runtime_checkable`
`Protocol` — no base class to inherit). `capabilities` may be empty; it only
feeds `CapabilitySelector`.

### Speaker selectors

| Selector | Strategy | On failure |
|---|---|---|
| `RoundRobinSelector` | Deterministic rotation in registration order | — |
| `LLMManagerSelector(llm_service, transcript_tail=10)` | A manager model reads the roster (name + capabilities), the topic and the transcript tail, and names the next speaker via strict JSON with its **reasoning first** | Fails open to round-robin on LLM error, malformed JSON, or an unknown name — a flaky manager slows the conversation, it never ends it (each fallback logs a warning) |
| `CapabilitySelector` | Keyword match of the last message (the topic, before anyone spoke) against participant capability tokens; highest overlap speaks next | No overlap anywhere falls back to round-robin |

Custom strategies implement the `SpeakerSelector` protocol:
`async select(participants, topic, transcript) -> Participant`.

### Bounded three ways

An emergent conversation is still a loop, and loops end. Every chat is
bounded by:

1. **`max_rounds`** (default `8`) — hard cap on utterances
   (`terminated_by: "max_rounds"`).
2. **`terminate`** — optional caller predicate over the transcript, checked
   after every utterance; `True` ends the chat
   (`terminated_by: "predicate"`).
3. **`budget`** — optional
   [`LoopBudget`](orchestration.md) ticked once per round. Exhaustion ends
   the chat **cleanly** with `terminated_by: "budget"` rather than raising,
   so the partial transcript is preserved.

All group-chat symbols (`GroupChat`, `GroupChatResult`, `ChatMessage`,
`Participant`, `SpeakerSelector` and the three selectors) are exported from
`core.agent`.
