---
title: Agentic Patterns
description: Agentic patterns implemented in BaselithCore
---

BaselithCore implements **20+ agentic design patterns** organized into 7 categories. Each pattern has a dedicated module in `core/`.

---

## Pattern Overview

The **agentic patterns** are organized into 7 functional categories:

| #   | Pattern                | Category       | Module              | Brief Description                         |
| --- | ---------------------- | -------------- | ------------------- | ----------------------------------------- |
| 1   | **Reflection**         | Control        | `core/reflection/`  | Self-evaluation and response refinement   |
| 2   | **Guardrails**         | Control        | `core/guardrails/`  | Input/output protection (security)        |
| 3   | **Goals**              | Control        | `plugins/goals/`    | Goal and sub-goal system                  |
| 4   | **Planning**           | Logic          | `core/planning/`    | Task decomposition into steps             |
| 5   | **Reasoning (ToT)**    | Logic          | `core/reasoning/`   | Chain/Tree-of-Thought reasoning           |
| 6   | **Self-Correction**    | Logic          | `core/reasoning/`   | Auto-correction of errors                 |
| 7   | **Swarm Intelligence** | Social         | `core/swarm/`       | Multi-agent coordination                  |
| 8   | **A2A Protocol**       | Social         | `core/a2a/`         | Agent-to-agent communication              |
| 9   | **Human-in-the-Loop**  | Social         | `core/human/`       | Human intervention for critical decisions |
| 10  | **MCP Integration**    | Social         | `core/mcp/`         | Tool calling via Model Context Protocol   |
| 11  | **World Model**        | World          | `core/world_model/` | World state representation                |
| 12  | **Exploration**        | World          | `core/exploration/` | Autonomous information exploration        |
| 13  | **Adversarial**        | World          | `core/adversarial/` | Adversarial scenario simulation           |
| 14  | **Personas**           | Identity       | `core/personas/`    | Configurable personalities                |
| 15  | **Meta-Agent**         | Identity       | `core/meta/`        | Agent coordinating other agents           |
| 16  | **Learning Loop**      | Learning       | `core/learning/`    | Continuous learning from interactions     |
| 17  | **Evolution**          | Learning       | `core/learning/`    | Evolutionary prompt improvement           |
| 18  | **Auto Fine-Tuning**   | Learning       | `core/finetuning/`  | Automatic fine-tuning from feedback       |
| 19  | **Memory Tiering**     | Infrastructure | `core/memory/`      | Multi-level memory system                 |
| 20  | **Multi-Tenancy**      | Infrastructure | `core/context.py`   | Data isolation between tenants            |
| 21  | **Task Queue**         | Infrastructure | `core/task_queue/`  | Distributed queues for async jobs         |
| 22  | **Evaluation**         | Infrastructure | `core/services/evaluation/` | LLM response quality evaluation       |

### Distribution by Category

- **Control**: 3 patterns (Reflection, Guardrails, Goals)
- **Logic**: 3 patterns (Planning, Reasoning, Self-Correction)
- **Social**: 4 patterns (Swarm, A2A, Human-in-Loop, MCP)
- **World**: 3 patterns (World Model, Exploration, Adversarial)
- **Identity**: 2 patterns (Personas, Meta-Agent)
- **Learning**: 3 patterns (Learning, Evolution, Fine-Tuning)
- **Infrastructure**: 4 patterns (Memory, Multi-Tenancy, Task Queue, Evaluation)

**Total**: 20+ agentic patterns

---

## Control Patterns

### Reflection

**Module**: `core/reflection/`

The agent evaluates and improves its own responses through a self-evaluation loop.

```python
from core.reflection import ReflectionAgent, DefaultEvaluator, DefaultRefiner

# ReflectionAgent requires an evaluator and a refiner
agent = ReflectionAgent(evaluator=DefaultEvaluator(), refiner=DefaultRefiner())

# Evaluate, then iteratively refine until the quality threshold is met.
# reflect() returns (final_response, final_evaluation, iterations_used).
final_response, evaluation, iterations = await agent.reflect(
    response=initial_response,
    query=query,
)

# Or run the steps manually
evaluation = await agent.evaluate(response=initial_response, query=query)
if evaluation.score < 0.8:
    refined = await agent.refine(
        response=initial_response,
        feedback=evaluation.feedback,
        query=query,
    )
```

**Components**:

| File            | Description                                            |
| --------------- | ----------------------------------------------------- |
| `agent.py`      | `ReflectionAgent` (orchestrates evaluate/refine loop) |
| `evaluators.py` | `SelfEvaluator` protocol + `DefaultEvaluator`         |
| `refiners.py`   | `Refiner` protocol + `DefaultRefiner`                 |
| `protocols.py`  | Interfaces                                             |

---

### Guardrails

**Module**: `core/guardrails/`

Input/output protection for security and quality.

```python
from core.guardrails import InputGuard, OutputGuard

input_guard = InputGuard()
output_guard = OutputGuard()

# Validate user input (validate_async is available for async pipelines)
result = input_guard.validate(user_input)
if not result.is_valid:
    return "Invalid input: " + (result.blocked_reason or "blocked")

clean_input = result.sanitized_input or user_input

# Process...
response = await agent.generate_response(clean_input)

# Filter output before sending
output = output_guard.filter(response)
safe_output = output.filtered_output
```

**Input Checks**:

- Injection detection (prompt injection, jailbreak)
- PII detection and masking
- Toxic content filtering

**Output Checks**:

- Hallucination detection
- Sensitive data leakage
- Format validation

!!! note "Applied in the loop — streaming included"
    `Orchestrator.process` runs both guards automatically
    (`core/orchestration/guard_pipeline.py`), and `process_stream` filters
    streamed chunks through `guard_stream`
    (`core/orchestration/stream_guard.py`) with a holdback window, so
    redaction patterns split across chunk boundaries are still caught. The
    inbound gate (`guard_input_async`) also layers **content moderation**
    (`core/guardrails/moderation.py`, fail-open) on top of the regex guard
    when `BASELITH_MODERATION_PROVIDER` names a provider. Moderation of the
    **output** side is a second opt-in (`BASELITH_MODERATION_OUTPUT=true` —
    one extra moderation call per response): `guard_output_async` replaces a
    flagged final response, and `moderate_stream` re-checks the accumulated
    stream every 512 buffered characters, aborting a flagged stream (text
    already emitted cannot be recalled). See
    [Orchestration › Content guard pipeline](../core-modules/orchestration.md#content-guard-pipeline-guard_pipelinepy)
    and [Guardrails › Content Moderation](../core-modules/guardrails.md#content-moderation).

---

### Goals

**Module**: `plugins/goals/` (re-exported from `core/goals/` for backward compatibility)

Goal and sub-goal system for complex tasks. Moved to `plugins/` per the Core Sacro Rule (domain logic lives in plugins).

```python
from plugins.goals import GoalTracker, Goal, GoalStatus

tracker = GoalTracker()

# Define main goal
main_goal = Goal(
    name="analyze_market",
    description="Analyze market trends",
    success_criteria=["data_collected", "trends_identified", "report_generated"]
)

# Decompose into sub-goals
await tracker.decompose(main_goal)

# Execute progressively
while not tracker.is_complete():
    next_goal = tracker.get_next()
    result = await agent.execute(next_goal)
    tracker.mark_complete(next_goal, result)
```

!!! note "Backward Compatibility"
    `core/goals/` still works as a re-export shim — existing imports are not broken.

---

## Logic Patterns

### Planning

**Module**: `core/planning/`

Decomposition of complex tasks into executable steps. Plans can be converted to executable workflows via `plan_to_workflow()`.

```python
from core.planning import TaskPlanner, plan_to_workflow
from core.workflows import WorkflowExecutor

planner = TaskPlanner()

# Create a dependency-aware plan
plan = await planner.create_plan(
    goal="Build a TODO web app",
    context={"framework": "FastAPI"},
    max_steps=8,
)

# Convert Plan → WorkflowDefinition and execute
workflow = plan_to_workflow(plan)
executor = WorkflowExecutor()
result = await executor.execute(workflow, initial_input={"project": "todos"})
```

**Action mapping**: Each `PlanStep.action` is mapped to a `NodeType` — `analyze`/`execute`/`validate` → `AGENT`, `check`/`condition` → `CONDITION`, `transform` → `TRANSFORM`, `tool` → `TOOL`.

---

### Reasoning (Tree of Thoughts)

**Module**: `core/reasoning/`

Advanced reasoning with Chain-of-Thought and Tree-of-Thoughts.

```python
from core.reasoning import TreeOfThoughts, ChainOfThought

# Chain of Thought (linear). reason() returns (final_answer, steps).
cot = ChainOfThought()
answer, steps = await cot.reason(
    question="If I have 3 apples and eat 1, how many remain?",
)

# Tree of Thoughts (branching exploration via MCTS or BFS)
tot = TreeOfThoughts()
result = await tot.solve(
    problem="How to optimize system performance?",
    k=3,            # branching factor
    max_steps=4,    # max tree depth
    strategy="mcts",
)
print(result["solution"])
```

**ToT Components**:

| File            | Description                                            |
| --------------- | ------------------------------------------------------ |
| `tot/tree.py`   | `ThoughtNode` + Mermaid export helpers                  |
| `tot/mcts.py`   | `uct_select`, `backpropagate`, `mcts_search[_async]`     |
| `tot/engine.py` | `TreeOfThoughts` / `TreeOfThoughtsAsync` solve loop      |
| `tot/cache.py`  | `ThoughtCache` (LRU + TTL)                              |

!!! warning "ToT is the most expensive pattern in the loop"
    One async MCTS iteration is a batched generation call plus
    `branching_factor` evaluation calls, all serialized — about **120 LLM round
    trips** at the defaults (`iterations=30`, `branching_factor=3`). Two guards
    keep that inside the request: `mcts_search_async` checks the ambient
    `LoopBudget` deadline every iteration (the orchestrator ticks the budget
    once for the whole flow, so nothing inside the search consulted it before),
    and it stops after `patience` expansions without a better best node
    (`DEFAULT_PATIENCE = 8`). Route to ToT deliberately — see
    [Reasoning](../core-modules/reasoning.md#deadline-and-convergence-bounds-on-async-mcts).

---

### Self-Correction

**Module**: `core/reasoning/self_correction.py`

The agent autonomously corrects its own errors.

```python
from core.reasoning import SelfCorrector

corrector = SelfCorrector()

response = await agent.generate_response(query)

# Verify and correct. correct() returns a CorrectionResult with
# original/corrected/corrections_made/is_valid.
result = await corrector.correct(response=response, context=context)

if result.corrections_made > 0:
    response = result.corrected
    log.info(f"Applied {result.corrections_made} self-corrections")
```

---

## Social Patterns

### Swarm Intelligence

**Module**: `core/swarm/`

Multi-agent coordination inspired by collective behaviors.  Supports both single-task auction allocation and **batch parallel execution**.

```python
from core.swarm import Colony, AgentProfile, Capability, Task

colony = Colony()

# Register specialized agents
colony.register_agent(AgentProfile(
    id="researcher", name="Researcher",
    capabilities=[Capability(name="search", proficiency=0.9)],
))
colony.register_agent(AgentProfile(
    id="analyst", name="Analyst",
    capabilities=[Capability(name="analysis", proficiency=0.85)],
))

# Single task — auction allocation
winner = await colony.submit_task(
    Task(description="Summarize paper", required_capabilities=["search"])
)

# Batch parallel execution
tasks = [Task(id=f"t{i}", description=f"Sub-task {i}",
              required_capabilities=["search"]) for i in range(3)]

result = await colony.execute_batch(tasks, execute_fn=my_handler)
print(result.completed)   # {task_id: output, ...}
print(result.failed)      # {task_id: error_msg, ...}
print(result.unassigned)  # [task_ids with no capable agent]
```

**Strategies**:

- **Auction**: Agents "bid" for tasks based on capabilities and load
- **Pheromone**: Indirect communication via virtual signals that decay over time
- **Team Formation**: Dynamic team assembly for complex tasks
- **Batch Execution**: `execute_batch()` allocates all tasks via auction, then runs them concurrently with `asyncio.gather`

---

### A2A Protocol

**Module**: `core/a2a/`

Agent-to-Agent protocol for inter-agent communication.

```python
from core.a2a import A2AClient, AgentCard, AgentCapability, AgentDiscovery

# Describe an agent and register it for discovery
card = AgentCard(
    name="Data Analyzer",
    description="Analyzes and visualizes data",
    url="http://localhost:8001",
    capabilities=[
        AgentCapability(name="data_analysis", description="Analyze datasets"),
        AgentCapability(name="summarization", description="Summarize documents"),
    ],
)

discovery = AgentDiscovery()
discovery.register(card)

# Find agents by capability
agents = discovery.find_by_capability("summarization")

# Invoke a method on a remote agent (A2AClient wraps a target AgentCard)
client = A2AClient(agent_card=agents[0])
response = await client.invoke(
    method="summarize",
    params={"document": doc},
)
print(response.success, response.result)
```

---

### Human-in-the-Loop

**Module**: `core/human/`

Human intervention for critical decisions.

```python
from core.human import HumanIntervention

intervention = HumanIntervention()

# Request approval for sensitive actions. request_approval returns a bool
# (True if approved, False if rejected or timed out).
if action.is_sensitive:
    approved = await intervention.request_approval(
        action_description="This action will modify production data",
        timeout=300,
        context={"action": action.name},
    )

    if approved:
        await execute_action(action)
    else:
        log.info("Action rejected or timed out")
```

Other interaction primitives on `HumanIntervention`: `ask_input()`,
`request_selection()` and `notify()`. Pending requests can be inspected with
`get_pending_requests()` / `has_pending_requests()`.

!!! note "Durable approval gates — ReAct and graphs alike"
    Where no synchronous channel exists, approval pauses are **durable**
    (persist `awaiting_approval`, raise `ApprovalPendingError`, resume via
    the `/approvals` API) — the same contract for the ReAct autonomy gate and
    for `HUMAN` nodes in workflow graphs (`WorkflowBuilder.human(...)`, which
    fail **closed** without a checkpoint). See
    [Orchestration › Durable approvals](../core-modules/orchestration.md#durable-human-in-the-loop-approvals-pause-decide-resume)
    and [Workflows › Human approval gates](../core-modules/workflows.md#human-approval-gates-human-nodes).

---

### MCP Integration

**Module**: `core/mcp/`

Model Context Protocol for standardized tool calling.

Connect to an external MCP server as a client and invoke its tools:

```python
from core.mcp import MCPClient

# Connect to an MCP server (a launchable script or custom command)
async with MCPClient(server_script="path/to/server.py") as client:
    await client.connect()

    tools = await client.list_tools()  # discover available tools

    # Execute a tool exposed by the server
    result = await client.call_tool(
        "web_search",
        {"query": "latest AI news"},
    )
```

To **expose** your own functions as MCP tools, register them on an `MCPServer`
through the `MCPToolAdapter`:

```python
from core.mcp import MCPServer, MCPToolAdapter

server = MCPServer()
adapter = MCPToolAdapter(server)

async def web_search(query: str) -> str:
    """Search the web."""
    ...

adapter.register_function(web_search)  # name/description inferred from the func
```

---

## World Patterns

### World Model

**Module**: `core/world_model/`

Internal representation of world state.

The world model is built from `State` and `Action` value objects plus a
`StatePredictor` (LLM- or rule-based). `MCTSSimulator` and `RiskAssessor`
operate over the same primitives.

```python
from core.world_model import State, Action, StatePredictor, RiskAssessor

# Describe the current world state
state = State(name="cluster", variables={"status": "running", "load": 0.7})

# Define an action with effects
deploy = Action(
    name="deploy_new_version",
    effects={"load": 0.9},
)

# Predict the resulting state
predictor = StatePredictor()
next_state = await predictor.predict(state, deploy)
print(next_state.get("load"))

# Assess the risk of taking the action (returns a dict with score/level/details)
risk = RiskAssessor()
assessment = risk.assess_action(deploy, state=state)
```

---

### Exploration

**Module**: `core/exploration/`

Autonomous exploration to discover new information.

`ProactiveExplorer` searches a topic across a list of `KnowledgeSource`
implementations, expanding the query for broader coverage.

```python
from core.exploration import ProactiveExplorer

# sources implement the KnowledgeSource protocol (search/get_related)
explorer = ProactiveExplorer(sources=my_sources)

# Explore a topic
result = await explorer.explore(
    topic="competitor_analysis",
    depth=3,
    max_results=10,
)
print(result.findings)
```

---

### Adversarial

**Module**: `core/adversarial/`

Adversarial scenario simulation for robustness testing. The `RedTeamAgent` supports two detection strategies:

- **LLM-based semantic detection** (default): An LLM judges whether an attack succeeded based on the response semantics.
- **Keyword heuristic fallback**: Pattern-matching on known attack-success indicators when LLM is unavailable.

```python
from core.adversarial import RedTeamAgent, AttackCategory

# LLM-based detection (default)
red_team = RedTeamAgent(llm_detection=True)

# Full attack across attack categories. target_fn is an async callable
# (prompt -> response). Returns a SecurityReport.
report = await red_team.attack(
    target_fn=my_agent_fn,
    target_name="my_agent",
)
print(f"Vulnerabilities found: {len(report.vulnerabilities)}")

# Scope the attack to specific categories
report = await red_team.attack(
    target_fn=my_agent_fn,
    categories=[AttackCategory.PROMPT_INJECTION, AttackCategory.JAILBREAK],
)

# Quick scan with a minimal attack set (returns a summary dict)
summary = await red_team.quick_scan(target_fn=my_agent_fn)
```

**Detection flow**: `_analyze_attack_success()` tries `_analyze_with_llm()` first. If the LLM call fails or `llm_detection=False`, it falls back to `_analyze_with_keywords()`.

---

## Identity Patterns

### Personas

**Module**: `core/personas/`

Configurable personalities for agents.

```python
from core.personas import Persona, PersonaManager

manager = PersonaManager()

# Define persona
expert = Persona(
    name="TechExpert",
    traits=["technical", "precise", "detailed"],
    tone="formal",
    expertise=["software", "AI", "cloud"]
)

# Apply to an agent
agent.set_persona(expert)
```

---

### Meta-Agent

**Module**: `core/meta/`

Agent that coordinates and optimizes other agents. Includes a **multi-perspective debate** system with LLM-powered agreement analysis.

```python
from core.meta import MultiPersonaAgent, InternalDebate, PersonaEnsemble

# End-to-end: ensemble generates perspectives, then they debate, then synthesize
meta = MultiPersonaAgent()
response = await meta.process("Best caching strategy for this workload?")
print(response.final_answer, response.confidence)
print(response.debate_result.consensus_level)

# Or drive the debate directly over Perspective objects
ensemble = PersonaEnsemble()
perspectives = await ensemble.generate_perspectives("Best caching strategy?")

debate = InternalDebate(max_rounds=3, consensus_threshold=0.7)
result = await debate.run(perspectives, query="Best caching strategy?")
print(result.winning_perspective, result.key_points, result.unresolved_tensions)
```

**Debate internals**: `_find_agreements_disagreements()` uses LLM semantic analysis to extract structured agreements/disagreements from free-text perspectives, with a keyword-heuristic fallback. `_determine_winner()` scores perspectives by confidence and debate-round citation frequency.

---

## Learning Patterns

### Learning Loop

**Module**: `core/learning/`

Continuous learning from interactions.

```python
from core.learning import ContinuousLearner

learner = ContinuousLearner()

# Record an experience (sync; the reward is computed internally by the
# reward model). Returns the recorded Experience.
experience = learner.record_experience(
    state={"context": "..."},
    action="search_web",
    outcome="found 3 results",
    success=True,
)

# Periodically train the policy from buffered experiences
stats = learner.train(iterations=10)
```

---

### Evolution

**Module**: `core/learning/evolution.py`

`EvolutionService` is an event-driven service: it subscribes to evaluation-
completed events and, when configured, drives automatic fine-tuning through
`AutoFineTuningService`.

```python
from core.learning import EvolutionService

evolution = EvolutionService(enable_auto_finetuning=True)

# Start listening for evaluation events
evolution.start()

# Inspect aggregated evolution stats
stats = evolution.get_evolution_stats()

# Manually kick off a fine-tuning cycle (returns a job id, if available)
job_id = await evolution.trigger_manual_finetuning()

evolution.stop()
```

---

### Auto Fine-Tuning

**Module**: `core/finetuning/`

Automatic fine-tuning based on feedback.

Fine-tuning runs are executed through `FineTuningPipeline`, which dispatches to
the configured provider (OpenAI or Together). Build a dataset with
`DatasetBuilder` and start training:

```python
from core.finetuning import (
    FineTuningPipeline,
    DatasetBuilder,
    FineTuneConfig,
)

pipeline = FineTuningPipeline()

# Build a training dataset from collected examples
dataset = DatasetBuilder()
dataset.add_conversation(user_message="...", assistant_response="...")

# Start training (returns a FineTuneResult with the job handle)
result = await pipeline.start_training(
    training_file=dataset,
    config=FineTuneConfig(base_model="gpt-4o-mini-2024-07-18"),
)

if result.success and result.job:
    status = await pipeline.get_job_status(result.job.id)
```

!!! note "Triggering"
    Automatic, feedback-driven triggering of fine-tuning jobs is handled by
    `AutoFineTuningService` in `core/learning/` (wired to evaluation events),
    which delegates the actual run to `FineTuningPipeline`.

---

## Infrastructure Patterns

### Memory Tiering

**Module**: `core/memory/`

Multi-level memory system.

| Level      | Storage   | Use                         |
| ---------- | --------- | --------------------------- |
| L1 Context | In-memory | Current conversation        |
| L2 Graph   | FalkorDB  | Relationships and knowledge |
| L3 Vector  | Qdrant    | Semantic search             |

```python
from core.memory import AgentMemory

memory = AgentMemory()

# Unified access
context = await memory.get_context_async(max_tokens=2000)
related = await memory.recall(query, limit=5)
```

---

### Multi-Tenancy

**Module**: `core/context.py`

Data isolation between tenants.

Tenant identity is propagated via `contextvars`. Set it at the entry point of a
request/task and reset it with the returned token (there is no context-manager
helper). `get_current_tenant_id()` reads the current value.

```python
from core.context import (
    set_tenant_context,
    reset_tenant_context,
    get_current_tenant_id,
)

# Set tenant for the request/task
token = set_tenant_context("tenant-123")
try:
    # All operations are isolated to the current tenant
    assert get_current_tenant_id() == "tenant-123"
    data = await repository.get_all()  # Only tenant data
finally:
    reset_tenant_context(token)
```

---

### Task Queue

**Module**: `core/task_queue/`

Distributed queues for asynchronous jobs.

```python
from core.task_queue import enqueue, TaskTracker

# Enqueue task
job_id = await enqueue(
    "document_ingestion",
    document_id=doc_id,
    priority="high"
)

# Monitor status
tracker = TaskTracker()
status = await tracker.get_status(job_id)
```

---

### Evaluation

**Module**: `core/services/evaluation/`

LLM response quality evaluation using 4 RAG metrics:

| Metric                 | When                   | Description                                          |
| ---------------------- | ---------------------- | ---------------------------------------------------- |
| `faithfulness`         | Always                 | How well the answer is grounded in retrieved context |
| `answer_relevancy`     | Always                 | How relevant the answer is to the query              |
| `contextual_precision` | With `expected_output` | Ranking quality of retrieved documents               |
| `contextual_recall`    | With `expected_output` | Coverage of ground-truth in retrieved context        |

```python
from core.services.evaluation.service import EvaluationService

evaluation = EvaluationService()

metrics = await evaluation.evaluate_rag_response(
    query=user_query,
    response=agent_response,
    retrieved_contexts=contexts,
    expected_output=ground_truth,  # enables precision/recall
)

print(f"Faithfulness: {metrics['faithfulness']}")
print(f"Precision:    {metrics['contextual_precision']}")
```

---

## Pattern Interaction Example

Here's how multiple patterns work together:

```python
from core.reflection import ReflectionAgent, DefaultEvaluator, DefaultRefiner
from core.guardrails import InputGuard, OutputGuard

# Setup
reflection_agent = ReflectionAgent(
    evaluator=DefaultEvaluator(),
    refiner=DefaultRefiner(),
)
input_guard = InputGuard()
output_guard = OutputGuard()

async def handle_complex_request(query: str, agent) -> str:
    # 1. Guardrails - validate input
    validation = input_guard.validate(query)
    if not validation.is_valid:
        return validation.blocked_reason or "Invalid input"
    clean_query = validation.sanitized_input or query

    # 2. Generate a response (e.g. via an orchestrated agent)
    result = await agent.generate_response(clean_query)

    # 3. Reflection - evaluate and self-correct
    evaluation = await reflection_agent.evaluate(response=result, query=clean_query)
    if evaluation.score < 0.8:
        result = await reflection_agent.refine(
            response=result,
            feedback=evaluation.feedback,
            query=clean_query,
        )

    # 4. Guardrails - filter output
    return output_guard.filter(result).filtered_output
```

---

## Summary Table

| Category       | Pattern         | Module              |
| -------------- | --------------- | ------------------- |
| Control        | Reflection      | `core/reflection/`  |
|                | Guardrails      | `core/guardrails/`  |
|                | Goals           | `plugins/goals/`    |
| Logic          | Planning        | `core/planning/`    |
|                | Reasoning       | `core/reasoning/`   |
|                | Self-Correction | `core/reasoning/`   |
| Social         | Swarm           | `core/swarm/`       |
|                | A2A Protocol    | `core/a2a/`         |
|                | Human-in-Loop   | `core/human/`       |
|                | MCP Integration | `core/mcp/`         |
| World          | World Model     | `core/world_model/` |
|                | Exploration     | `core/exploration/` |
|                | Adversarial     | `core/adversarial/` |
| Identity       | Personas        | `core/personas/`    |
|                | Meta-Agent      | `core/meta/`        |
| Learning       | Learning Loop   | `core/learning/`    |
|                | Evolution       | `core/learning/`    |
|                | Fine-Tuning     | `core/finetuning/`  |
| Infrastructure | Memory Tiering  | `core/memory/`      |
|                | Multi-Tenancy   | `core/context.py`   |
|                | Task Queue      | `core/task_queue/`          |
|                | Evaluation      | `core/services/evaluation/` |

---

## Runtime primitives — quick reference

The patterns above are backed by a set of small, dependency-light
primitives the orchestrator and handlers can pull in directly. They all
live under `core/` and stay out of the way of plugin code.

| Concern | Module | Key symbols | Mkdocs page |
|---------|--------|-------------|--------------|
| Iteration + cost cap | `core/orchestration/limits.py` | `LoopLimits`, `LoopBudget`, `BudgetExceededError` | [Orchestration](../core-modules/orchestration.md) |
| Tenant + identity USD cost budgets (post-paid metering, fail-open) | `core/quotas/cost_budgets.py`, `core/quotas/cost_enforcement.py` | `CostBudgetMixin`, `record_tenant_cost`, `check_identity_cost_budget`, `CostBudgetExceededError`, `enforce_tenant_cost_budget` | [Usage Quotas](../core-modules/quotas.md#tenant-usd-cost-budgets) |
| Content moderation gates — input, plus opt-in output/stream (fail-open) | `core/guardrails/moderation.py`, `core/orchestration/guard_pipeline.py`, `core/orchestration/stream_guard.py` | `OpenAIModerator`, `get_moderator`, `guard_input_async`, `guard_output_async`, `moderate_stream` | [Guardrails](../core-modules/guardrails.md#content-moderation) |
| Packaged prompt catalog (registry-served hot-path prompts, env-switched A/B) | `core/prompts/catalog.py`, `core/prompts/catalog/*.md` | `resolve_catalog_prompt`, `CATALOG_DIR`, `BASELITH_PROMPT_VARIANTS_<NAME>` | [Prompt Registry](../core-modules/prompts.md#packaged-catalog-prompts) |
| Workflow-as-handler bridge (durable node replay, `HUMAN` approval gates, crews/colonies as nodes) | `core/workflows/flow_handler.py`, `core/workflows/executor.py`, `core/workflows/adapters.py` | `WorkflowFlowHandler`, `WorkflowExecutor.execute(checkpoint=...)`, `WorkflowBuilder.human`, `CrewNodeAdapter`, `ColonyNodeAdapter` | [Workflow Engine](../core-modules/workflows.md#orchestrator-bridge-workflowflowhandler) |
| Declarative agent spec | `core/orchestration/contract.py` | `AgentContract`, `ContractValidator`, `load_contract` | [Orchestration](../core-modules/orchestration.md) |
| Autonomy spectrum (incl. `self_modify` category) | `core/orchestration/autonomy.py` | `AutonomyLevel`, `AutonomyPolicy`, `AutonomyUpgradeGate`, `enforce_approval`, `ApprovalRequiredError`, `SELF_MODIFY` | [Orchestration](../core-modules/orchestration.md) |
| Tool-invocation chokepoint (contract → autonomy → budget → rate limit → pre-hooks; audited) | `core/orchestration/enforcement.py`, `hooks.py`, `rate_limit.py` | `enforce_tool_invocation`, `ToolHookRegistry`, `get_tool_hook_registry`, `ToolRateLimiter` | [Orchestration](../core-modules/orchestration.md#tool-hooks-hookspy) |
| Indirect-injection scan of tool observations (opt-in `BASELITH_INDIRECT_SCAN_TOOL_OUTPUT`) | `core/orchestration/tool_output.py`, `core/guardrails/indirect.py` | `sanitize_tool_output`, `scan_external_content` | [Guardrails](../core-modules/guardrails.md#indirect-injection-scanning) |
| Agentic-vs-deterministic router | `core/orchestration/task_classifier.py` | `TaskClassifier`, `RoutingRecommendation` | [Orchestration](../core-modules/orchestration.md) |
| Durable checkpoint + idempotent replay (on by default) | `core/orchestration/checkpoint.py`, `checkpoint_memory.py`, `checkpoint_postgres.py`, `checkpoint_sqlite.py`, `checkpoint_factory.py` | `Checkpoint`, `CheckpointStore`, `CheckpointManager.run_step`, `InMemoryCheckpointStore`, `PostgresCheckpointStore`, `SQLiteCheckpointStore` | [Orchestration](../core-modules/orchestration.md) |
| Structured run events + cross-replica SSE fan-out (opt-in `BASELITH_RUN_EVENTS_BRIDGE=redis`) | `core/orchestration/run_events.py`, `run_events_bridge.py` | `publish_run_event`, `stream_run_events`, `set_run_event_broadcaster`, `RedisRunEventsBridge` | [Orchestration](../core-modules/orchestration.md#structured-run-event-streaming-astream-events-equivalent) |
| Streamed-output guarding (holdback window + opt-in moderation) | `core/orchestration/stream_guard.py` | `guard_stream`, `DEFAULT_HOLDBACK`, `moderate_stream`, `MODERATION_CHECK_INTERVAL` | [Orchestration](../core-modules/orchestration.md#streaming-pipeline) |
| Crash-recovery sweep (one per fleet, cross-replica locked) | `core/orchestration/recovery.py`, `core/api/_recovery_startup.py` | `resume_interrupted_runs`, `RecoveryReport`, `start_checkpoint_recovery` | [Orchestration](../core-modules/orchestration.md) |
| Tool/skill envelope | `core/plugins/result.py` | `SkillResult`, `ok`, `fail`, `partial` | [Plugins](../core-modules/plugins.md) |
| Concurrent multi-tool turn (gates stay sequential; durable runs execute sequentially for checkpoint replay) | `core/reasoning/react_tools.py` | `ToolExecutionMixin._execute_tool_calls`, `MAX_PARALLEL_TOOL_CALLS` | [Reasoning](../core-modules/reasoning.md#concurrent-multi-tool-turns) |
| Declarative SKILL.md skills (progressive disclosure) | `core/plugins/declarative.py`, `core/plugins/skills_service.py` | `DeclarativeSkillLoader`, `SkillCard`, `SkillService`, `make_activation_tool_fn` | [Declarative Skills](../core-modules/skills.md) |
| Section-bounded scratchpad | `core/memory/scratchpad.py` | `Scratchpad`, `InMemoryScratchpadBackend` | [Memory](../core-modules/memory.md) |
| Hybrid keyword/dense retrieval | `core/memory/hybrid_search.py` | `BM25Index`, `HybridSearcher`, `ScoredHit` | [Memory](../core-modules/memory.md) |
| Trajectory eval + CI runner (incl. `reference_fact` groundedness, multi-model bake-off) | `core/evaluation/trajectory.py`, `core/evaluation/regression_runner.py`, `core/evaluation/bake_off.py` | `TrajectoryEvaluator`, `RegressionReport`, `run_bake_off`, `BakeOffResult` | [Evaluation](../core-modules/evaluation.md) |
| Engineered loop (verifier-owned iteration) | `core/loops/engineered.py`, `stall.py`, `lessons.py`, `fingerprint.py` | `EngineeredLoop`, `StallGuard`, `LessonLog`, `failure_fingerprint` | [Loop Engineering](../core-modules/loops.md) |
| Loop-as-handler bridge (budget-bound, checkpointed outcome, default escalation) | `core/loops/flow_handler.py`, `escalation.py`, `rubric.py`, `goal.py` | `LoopFlowHandler`, `build_default_escalation`, `rubric_verifier`, `harden_goal` | [Loop Engineering](../core-modules/loops.md#production-wiring-flow_handlerpy) |
| Handoff cycle guard (no revisit, hop-capped) | `core/swarm/types.py`, `core/config/swarm.py` | `HandoffCycleGuard`, `Handoff.hop_count` / `.visited`, `SwarmConfig.max_handoff_hops` | [Swarm Intelligence](../core-modules/swarm.md) |
| Governed self-modification (eval gates + `self_modify` approval + audit) | `core/skill_evolution/gating.py`, `core/skill_evolution/types.py`, `core/optimization/tune_gate.py` | `SkillGate`, `FitnessVector`, `review_candidate`, `TuneEvaluator` | [Skill Evolution](../core-modules/skill-evolution.md#governed-self-modification) |
| Multi-judge consensus | `core/evaluation/consensus.py` | `ConsensusEvaluator` | [Evaluation](../core-modules/evaluation.md) |
| Red-team regression gate | `core/evaluation/red_team.py`, `evals/red_team/` | `load_red_team_cases`, `run_red_team_suite` | [Evaluation](../core-modules/evaluation.md) |
| Outcome-fed model routing | `core/models/routing_stats.py` | `RoutingScoreboard`, `LearnedModelRouter` | [Domain Models](../core-modules/models.md) |
| Generator-Challenger debate | `core/meta/generator_challenger.py` | `GeneratorChallengerProtocol`, `Verdict` | [Meta-Agent & Debate](../core-modules/meta.md) |
| Few-shot example library | `core/personas/few_shot.py` | `FewShotLibrary`, `FewShotExample`, `load_library` | [Personas](../core-modules/personas.md) |
| LLM portability layer | `core/models/pricing.py`, `routing.py`, `fallback.py` | `ModelRouter`, `FallbackChain`, `estimate_cost` | [Chat & RAG](../core-modules/chat.md) |
| Central per-plugin LLM policy | `core/services/llm/policy.py`, `runtime.py`, `core/middleware/plugin_context.py` | `PluginLLMPolicy`, `set_plugin_llm_policy_resolver`, `get_llm_service` | [Services](../core-modules/services.md) |
| A2UI blueprint schema | `core/a2a/a2ui.py` | `A2UIBlueprint`, `validate_blueprint`, component models | [A2A Protocol](../core-modules/a2a.md) |
| Signed mandate chain (money math in integer cents; signed conditions enforced, unknown keys fail closed) | `core/world_model/mandates.py`, `core/world_model/mandate_conditions.py`, `core/world_model/replay_guard.py` | `IntentMandate`, `CartMandate.total_cents`, `verify_chain` (`expected_merchant_id`, `max_cart_age_seconds`), `evaluate_intent_conditions`, `InMemoryReplayGuard`, `RedisReplayGuard` | [World Model](../core-modules/world-model.md#signed-intent-conditions) |
| Loop instrumentation on `AgentState` | `core/chat/agent_state.py` | `iteration_count`, `cost_usd`, `trajectory`, `record_tool_call()` | [Chat & RAG](../core-modules/chat.md) |
| OpenInference enrichment of LLM spans (opt-in, content capture separate) | `core/observability/openinference.py` | `openinference_llm_attributes`, `openinference_enabled`, `MAX_CONTENT_CHARS` | [Observability](../core-modules/observability-module.md#openinference-enrichment-openinferencepy) |

!!! note "Eval gating in CI"
    The three deterministic quality gates backed by the eval primitives above
    — `evals`, `red_team`, `fairness` — sit in `python_test`'s `needs` graph
    in `.github/workflows/ci.yml`, so they block the test→release path
    **structurally**, not only when configured as required checks in branch
    protection. Nondeterministic **LLM-as-judge** scoring runs on a schedule
    instead of gating merges: `.github/workflows/nightly-evals.yml` invokes
    `scripts/run_judge_evals.py` (judge: `RelevanceEvaluator`), which skips
    green when no provider credentials are configured, exits `1` on a
    pass-rate regression, and uploads a JSON report artifact. Details:
    [Evaluation](../core-modules/evaluation.md#the-shipped-ci-gate).
