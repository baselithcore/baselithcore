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
  answers without tool calls, bounded by `max_iterations`. A sync callable
  runs in a worker thread, every call is bounded by `tool_timeout`, and the
  calls of one turn overlap — see
  [How a tool call runs](#how-a-tool-call-runs).
- **A real message history** — the loop keeps a
  [neutral `Message` history](messages.md) and only ever appends to it: the
  assistant turn goes back **verbatim** (thinking blocks included), and every
  tool result of that turn returns in one user message as a `tool_result` block
  carrying the `tool_use_id` it answers and an `is_error` flag when the call
  failed. That is what keeps parallel calls correlated, keeps a failure legible
  as one, and keeps the prompt prefix byte-stable so the provider's prompt cache
  can serve it. `AgentResult.messages` is the conversation the loop actually
  sent, oldest first. A service that predates the message API still works — the
  history is flattened into a transcript for it. The round trip itself is
  [`generate_over_messages`](services.md#one-round-trip-for-a-message-history)
  (`core/services/llm/message_transport.py`), now shared with the
  [ReAct loop](reasoning.md#the-turn-is-a-message-not-a-rebuilt-prompt) —
  same transport, same degradation, no behaviour change on this side.
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
| `autonomy_policy` | `None` | When set, tools whose category needs approval at the active level are gated through the enforcement chokepoint. Left `None` deliberately: there is no ambient policy to inherit here, and defaulting to one would start demanding approval for every effectful tool of every existing typed agent, with no channel to approve on. |
| `tool_ledger` | `None` | With a stable `run_id`, every non-`read_only` tool is recorded before it executes and replayed instead of re-executed on a retry of the same run |
| `tool_timeout` | `120.0` (`DEFAULT_TOOL_TIMEOUT_SECONDS`) | Per-call wall-clock cap in seconds, shrunk further to whatever an ambient `LoopBudget` has left. `None` removes the cap — the behaviour before this release, where a tool that never returned pinned the agent |

`agent.tool_names` reads back the tools an agent is armed with — their names
in declaration order, as a `tuple[str, ...]`. It is the public counterpart of
the private `_tools` dict that callers (and the scaffolded project) used to
reach into.

## What `run()` raises

| Exception | When |
|---|---|
| `AgentOutputValidationError` | `output_type` was never satisfied within `max_retries`. |
| `RuntimeError` | `max_iterations` exhausted before a final answer. |
| `BudgetExceededError` | An ambient `LoopBudget` cap was hit. Fail-closed — a runaway loop cannot keep dispatching tools. |
| `ApprovalPendingError` | A tool needs a human decision that is not available yet; the run pauses **durably**. Only reachable when the agent was given an `autonomy_policy`; without one the approval gate is inert. |

The last two are new failure modes for callers that previously only had to handle
validation and iteration exhaustion. Both come from the shared enforcement
chokepoint (`core.orchestration.enforcement`), so an `Agent` embedded in an
orchestrated request is subject to exactly the same caps as any other path.

!!! tip "`run_id` is what makes deduplication possible"
    `agent.run(prompt, run_id=...)` is ignored unless a `tool_ledger` was
    supplied — and a *fresh* id per attempt is a different run by definition, so
    the ledger has nothing to match and every effectful tool executes again. Pass
    a **stable** id across retries of the same logical run.

## How a tool call runs

Resolution, argument validation and the gates live in
`core/agent/_tool_dispatch.py`; the execution itself lives in
`core/agent/_tool_runtime.py`, which gives the typed loop the three properties
the [ReAct executor](reasoning.md#concurrent-multi-tool-turns) already had:

- **Off the event loop.** A synchronous tool runs in a worker thread
  (`asyncio.to_thread`) instead of inline on the loop. `tools=[my_function]`
  invites ordinary blocking callables, and one blocking HTTP client or file
  read on the loop stalls every other in-flight request in the process for as
  long as it takes. Async tools are awaited directly, as before.
- **Bounded.** `tool_timeout` caps each call at `120.0` seconds by default,
  shrunk to whatever the ambient [`LoopBudget`](orchestration.md) has left so
  a tool cannot outlive the request whose answer it is computing. An elapsed
  deadline comes back to the model as a failed `tool_result` and the loop
  continues: the call failed, not the run.
- **Overlapped.** When one turn carries several tool calls, the model emitted
  all of them before seeing any result, so they are independent by
  construction. The approved ones run concurrently under a semaphore of
  `MAX_PARALLEL_TOOL_CALLS = 8` (`core/reasoning/react_tools.py`), so the turn
  costs the slowest call instead of the sum. Each result is written back at
  the index of the call that produced it, so the `tool_result` blocks stay in
  the order the model asked regardless of completion order.

!!! danger "Gates run strictly in order, before anything executes"
    Every call of the turn is resolved, argument-checked and gated in emission
    order, and only the survivors are then overlapped. The ordering is
    load-bearing: `ApprovalPendingError` and `BudgetExceededError` are
    fail-closed refusals that abort the turn, and a refusal is worthless if a
    later tool in the same turn has already run its side effect. Unknown-tool
    and denial observations are filled in at their own index.

!!! note "Ledger keys are unchanged"
    Each call's step is still its position in the run — assigned before
    anything executes rather than as results arrive — so a resumed run
    produces the same `tool_ledger` keys as the sequential loop this replaced.

!!! warning "A timeout stops the wait, not the thread"
    Cancelling on a deadline stops the *await*. A synchronous tool already
    running in a worker thread cannot be interrupted; Python has no mechanism
    for it. The agent stops waiting and reports the timeout, and bounding the
    work itself — a client-side timeout on your HTTP call, say — stays the
    tool's job.

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
