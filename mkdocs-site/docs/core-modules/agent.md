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
| `autonomy_policy` | standalone guard | Left at its default, tools **explicitly declared** `category="destructive"` are refused (see [Safe defaults for a standalone run](#safe-defaults-for-a-standalone-run)); every other tool runs as before. An `AutonomyPolicy` replaces the guard with the real approval gate: tools whose category needs approval at the active level go through the enforcement chokepoint. `None` is the explicit opt-out — no guard, gate inert. |
| `tool_ledger` | process-wide ledger | With a stable `run_id`, every non-`read_only` tool is recorded before it executes and replayed instead of re-executed on a retry of the same run. Defaults to the ledger `ORCHESTRATOR_TOOL_LEDGER` selected (`core/orchestration/ledger_factory.py`) — durable where the deployment has Postgres; pass one explicitly to override it |
| `tool_timeout` | `120.0` (`DEFAULT_TOOL_TIMEOUT_SECONDS`) | Per-call wall-clock cap in seconds, shrunk further to whatever an ambient `LoopBudget` has left. `None` removes the cap — the behaviour before this release, where a tool that never returned pinned the agent |
| `loop_limits` | orchestrator defaults | Caps for a run with no ambient `LoopBudget`: by default the orchestrator's `LoopLimits()` (`budget_usd`, `max_tool_calls`, `ORCHESTRATOR_LOOP_MAX_TOKENS`, `ORCHESTRATOR_LOOP_MAX_SECONDS`) with the iteration cap widened to `max_iterations`. An explicit `LoopLimits` replaces them; `None` disables the default budget. Under an ambient budget the default and `None` reuse it, while an explicit `LoopLimits` is still enforced through a nested child budget that also charges the ambient one (once). |

`agent.tool_names` reads back the tools an agent is armed with — their names
in declaration order, as a `tuple[str, ...]`. It is the public counterpart of
the private `_tools` dict that callers (and the scaffolded project) used to
reach into.

## What `run()` raises

| Exception | When |
|---|---|
| `AgentOutputValidationError` | `output_type` was never satisfied within `max_retries`. |
| `RuntimeError` | `max_iterations` exhausted before a final answer. |
| `BudgetExceededError` | A `LoopBudget` cap was hit — the ambient one, or the run's own default budget (`loop_limits`). Fail-closed — a runaway loop cannot keep dispatching tools. |
| `ApprovalPendingError` | A tool needs a human decision that is not available yet; the run pauses **durably**. Only reachable when the agent was given an `autonomy_policy`; without one the approval gate is inert. |

The last two are new failure modes for callers that previously only had to handle
validation and iteration exhaustion. Both come from the shared enforcement
chokepoint (`core.orchestration.enforcement`), so an `Agent` embedded in an
orchestrated request is subject to exactly the same caps as any other path.

!!! note "Effectful tools are deduplicated by default now"
    `tool_ledger=None` used to mean *no ledger at all*, so a typed agent re-ran
    a payment or an outbound webhook on every retry of the same `run_id` — the
    deduplication the runtime documents, missing from the surface the
    quickstart teaches. It now falls back to the process-wide ledger from
    `core/orchestration/ledger_factory.py`: durable where Postgres is
    configured, in-process (with a warning naming the consequence) where it is
    not. Set `ORCHESTRATOR_TOOL_LEDGER=off` to get the old behaviour back
    deployment-wide — see
    [Orchestration › Choosing the ledger](orchestration.md#choosing-the-ledger-orchestrator_tool_ledger).

!!! tip "`run_id` is what makes deduplication possible"
    The ledger stays inert unless `agent.run(prompt, run_id=...)` carries a
    **stable** id across retries of the same logical run: a *fresh* id per
    attempt is a different call by definition, so the ledger has nothing to
    match and every effectful tool executes again. Without a `run_id` it is
    never consulted at all.

!!! info "A retry may take a different path — it still replays"
    Ledger keys are content-addressed: tool, canonical arguments, tenant, the
    `run_id`, and how many identical calls the run already requested
    (`core/orchestration/call_keys.py`). A retried run whose model asks for
    `notify` before `charge_card` this time — or adds a lookup in between —
    replays both instead of charging again, while two deliberately identical
    calls in one run stay two effects. Rows written under the old positional
    key are still honoured when the retry takes its original path; see
    [Orchestration › Cross-process tool idempotency](orchestration.md#cross-process-tool-idempotency-idempotencypy).

!!! tip "Resume from a checkpoint: `agent.run(prompt, checkpoint=manager)`"
    Hand `run` a
    [`CheckpointManager`](orchestration.md#durable-resume-of-the-typed-agent)
    and each approved tool call is recorded through `run_step`; a resumed run
    replays the recorded observations without calling the tools, even with no
    durable ledger configured. `run_id` defaults to the checkpoint's. Turns run
    their calls sequentially in this mode. Without a checkpoint nothing
    changes.

## Safe defaults for a standalone run

!!! warning "Behaviour change"
    Two defaults changed for an `Agent` run **outside** an orchestrated
    request. `Orchestrator.process` already gave every request a SUPERVISED
    `AutonomyPolicy` and a `LoopBudget`; a standalone agent (a script, a worker,
    a `Crew`) had neither. Both changes are opt-out with one argument.

**Destructive tools are refused unless an approval policy is given.** A tool
whose author wrote `category="destructive"` — on a `ToolDefinition`, a
connector action or a declarative MCP tool — is not run (a connector
`ActionSpec` left at its default category counts as undeclared, like a
`ToolDefinition`). An `Agent` built inside an orchestrated request — by a plugin
handler, say — is not guarded either: the host owns approval policy there. The model receives an
`is_error` tool result saying the tool needs approval (and naming the opt-in
for the operator), and `agent_destructive_tool_refused` is logged. Nothing
else changes:

- plain callables (`tools=[my_function]`) and a `ToolDefinition` left at the
  default category still run. Their category is `destructive` for the ledger
  and the approval matrix, but it was never *declared*, and refusing them
  would break every existing typed agent. `ToolDefinition.category_declared`
  tells the two apart;
- `read_only`, `mutating`, `external_side_effect` and `self_modify` tools run
  exactly as before;
- `autonomy_policy=AutonomyPolicy(...)` (with a `human_intervention` channel or
  a `checkpoint` for durable approval) replaces the guard with the real gate,
  and a host that sets `agent._autonomy_policy` disarms it the same way;
- `autonomy_policy=None` restores the previous behaviour: no guard, no gate.

**A run with no ambient budget gets one.** Without a `LoopBudget` an
`Agent.run` was bounded by `max_iterations` alone — no dollar, token or
wall-clock cap. Now the run binds a budget built from the orchestrator's
defaults as the ambient one (`core.orchestration.budget_context.standalone_budget`),
so every LLM call is charged to it and every tool call counted; the iteration
cap is widened to `max_iterations`, so that stays the cap that governs the
loop. Inside an orchestrated request — or nested in another standalone run —
the ambient budget is reused, never doubled. `loop_limits=LoopLimits(...)`
sets the caps; `loop_limits=None` disables the default. Explicit caps are the
agent's own and hold under an ambient budget too: the run binds a child
budget (`standalone_budget(limits, enforce_own=True)`) that enforces them and
forwards every charge, token and tick once to the ambient budget, so
`Agent(loop_limits=LoopLimits(budget_usd=0.05))` inside a `Crew`, `GroupChat`,
swarm batch or orchestrated request still aborts at USD 0.05 while the
enclosing budget sees the spend (see
[Nested budgets](orchestration.md#nested-budgets-explicit-caps-under-an-ambient-budget)).

The same default applies to `GroupChat` (below) and to a swarm
`Colony.execute_batch` ([swarm](swarm.md)), with one difference: every
participant's `Agent.run` reuses that one shared budget, so its iteration and
tool-call counters are lifted (`shared_loop_limits()`) — otherwise the agents'
ticks would add up and end the chat or the batch early. The dollar, token and
wall-clock caps still apply. A `Crew` binds the same shared budget for its
whole `run()` — every task, and in `hierarchical` mode every manager turn — so
the dollar, token and wall-clock caps bound the crew rather than each task
separately (it used to get only the per-agent default per task, through each
task's `Agent.run`). `Crew(loop_limits=...)` mirrors `Colony(loop_limits=...)`:
left at the default it is `shared_loop_limits()`, an explicit `LoopLimits`
replaces it, and `None` opts out so each task's `Agent.run` binds its own
per-run default as before. An ambient budget always wins over the crew's
caps; an agent's own explicit `loop_limits` are still enforced inside it.

**Tool schemas are sent strict when they can be.** A tool whose parameter
schema already fits the strict dialect — every property required and typed,
extra keys forbidden (implicitly for a schema inferred from a signature,
which the argument validator already closes), only keywords both providers'
strict modes accept — goes out with `LLMToolSpec.strict=True`, so Anthropic
and OpenAI constrain the emitted arguments to the schema. Any other tool (an
optional argument, an untyped or bare `object` parameter, a `pattern` or
`minLength` constraint) is sent exactly as before. Providers without strict
tools (Ollama, Gemini) drop the flag; an OpenAI-compatible endpoint receives
the standard `strict` field (`core/agent/_strict_tools.py`).

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

!!! note "Ledger keys do not depend on completion order — or on position"
    Each call draws its occurrence (how many identical calls the run already
    requested) in emission order, before anything executes, so overlapping the
    calls cannot change their keys — and since the key carries no position, a
    resumed run matches them whatever order it asks in.

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
- **One crew-wide budget** — with no ambient budget, `run()` binds one shared
  `LoopBudget` for the whole crew (`loop_limits=`, default
  `shared_loop_limits()`: orchestrator dollar/token/wall-clock caps, counters
  lifted; `None` opts out; an explicit `LoopLimits` replaces it). A breach
  aborts the crew with `BudgetExceededError`.

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
3. **`budget`** — a [`LoopBudget`](orchestration.md) ticked once per
   round. Exhaustion ends the chat **cleanly** with `terminated_by: "budget"`
   rather than raising, so the partial transcript is preserved. Left at its
   default, a chat with no ambient budget binds one of its own (orchestrator
   defaults) as the ambient budget, so every participant's LLM call is
   charged to it; it is checked for its deadline each round rather than
   ticked (`max_rounds` already counts rounds, and typed-agent participants
   tick it themselves), and a breach inside a turn also ends the chat with
   `terminated_by: "budget"`. Inside an orchestrated request the ambient
   budget is used as is and its breach propagates. `budget=None` disables
   both.

All group-chat symbols (`GroupChat`, `GroupChatResult`, `ChatMessage`,
`Participant`, `SpeakerSelector` and the three selectors) are exported from
`core.agent`.
