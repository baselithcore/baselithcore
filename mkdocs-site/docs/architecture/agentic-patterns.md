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
| 22  | **Evaluation**         | Infrastructure | `core/evaluation/`  | LLM response quality evaluation           |

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
from core.reflection import ReflectionAgent

agent = ReflectionAgent()

# Generate initial response
response = await agent.generate(query)

# Evaluate and refine
evaluation = await agent.evaluate(response)
if evaluation.score < 0.8:
    response = await agent.refine(response, evaluation.feedback)
```

**Components**:

| File            | Description                                     |
| --------------- | ----------------------------------------------- |
| `agent.py`      | Main ReflectionAgent                            |
| `evaluators.py` | Evaluators (Relevance, Coherence, Faithfulness) |
| `refiners.py`   | Refinement strategies                           |
| `protocols.py`  | Interfaces                                      |

---

### Guardrails

**Module**: `core/guardrails/`

Input/output protection for security and quality.

```python
from core.guardrails import InputGuard, OutputGuard

input_guard = InputGuard()
output_guard = OutputGuard()

# Validate user input
safe_input = await input_guard.process(user_input)
if not safe_input.is_safe:
    return "Invalid input: " + safe_input.reason

# Process...
response = await agent.generate(safe_input.content)

# Validate output before sending
safe_output = await output_guard.process(response)
```

**Input Checks**:

- Injection detection (prompt injection, jailbreak)
- PII detection and masking
- Toxic content filtering

**Output Checks**:

- Hallucination detection
- Sensitive data leakage
- Format validation

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

# Chain of Thought (linear)
cot = ChainOfThought()
result = await cot.reason(
    problem="If I have 3 apples and eat 1, how many remain?",
    steps=3
)

# Tree of Thoughts (parallel exploration)
tot = TreeOfThoughts(
    branching_factor=3,
    max_depth=4
)
result = await tot.explore(
    problem="How to optimize system performance?",
    evaluation_fn=score_solution
)
```

**ToT Components**:

| File               | Description                        |
| ------------------ | ---------------------------------- |
| `tot/tree.py`      | Tree structure                     |
| `tot/node.py`      | Thought nodes                      |
| `tot/search.py`    | Search strategies (BFS, DFS, Beam) |
| `tot/evaluator.py` | Solution evaluation                |

---

### Self-Correction

**Module**: `core/reasoning/self_correction.py`

The agent autonomously corrects its own errors.

```python
from core.reasoning import SelfCorrector

corrector = SelfCorrector()

response = await agent.generate(query)

# Verify and correct
correction = await corrector.check_and_correct(
    query=query,
    response=response,
    context=context
)

if correction.was_corrected:
    response = correction.corrected_response
    log.info(f"Auto-correction: {correction.reason}")
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
from core.a2a import A2AClient, AgentCard

# Publish agent capabilities
card = AgentCard(
    agent_id="analyzer-001",
    name="Data Analyzer",
    capabilities=["data_analysis", "visualization"],
    endpoint="http://localhost:8001"
)

# Discover other agents
client = A2AClient()
agents = await client.discover(capability="summarization")

# Send request
response = await client.request(
    agent=agents[0],
    task="Summarize this document",
    payload={"document": doc}
)
```

---

### Human-in-the-Loop

**Module**: `core/human/`

Human intervention for critical decisions.

```python
from core.human import HumanApproval, ApprovalRequest

approval = HumanApproval()

# Request approval for sensitive actions
if action.is_sensitive:
    request = ApprovalRequest(
        action=action,
        reason="This action will modify production data",
        timeout_seconds=300
    )
    
    decision = await approval.request(request)
    
    if decision.approved:
        await execute_action(action)
    else:
        log.info(f"Action rejected: {decision.reason}")
```

---

### MCP Integration

**Module**: `core/mcp/`

Model Context Protocol for standardized tool calling.

```python
from core.mcp import MCPClient, Tool

client = MCPClient()

# Register tools
client.register_tool(Tool(
    name="web_search",
    description="Search the web",
    parameters={"query": "string"}
))

# Execute tool via MCP
result = await client.call_tool(
    "web_search",
    {"query": "latest AI news"}
)
```

---

## World Patterns

### World Model

**Module**: `core/world_model/`

Internal representation of world state.

```python
from core.world_model import WorldModel, Entity

model = WorldModel()

# Add entities
model.add_entity(Entity("user", attributes={"name": "Alice", "role": "admin"}))
model.add_entity(Entity("server", attributes={"status": "running", "load": 0.7}))

# Query state
users = model.query("entities WHERE type = 'user'")

# Update state
model.update("server", {"load": 0.9})

# Predict changes
prediction = await model.predict_next_state(action="deploy_new_version")
```

---

### Exploration

**Module**: `core/exploration/`

Autonomous exploration to discover new information.

```python
from core.exploration import Explorer

explorer = Explorer()

# Explore a domain
findings = await explorer.explore(
    domain="competitor_analysis",
    starting_points=["https://competitor.com"],
    depth=3,
    strategy="breadth_first"
)
```

---

### Adversarial

**Module**: `core/adversarial/`

Adversarial scenario simulation for robustness testing. The `RedTeamFramework` supports two detection strategies:

- **LLM-based semantic detection** (default): An LLM judges whether an attack succeeded based on the response semantics.
- **Keyword heuristic fallback**: Pattern-matching on known attack-success indicators when LLM is unavailable.

```python
from core.adversarial.red_team import RedTeamFramework, AttackCategory

# LLM-based detection (default)
framework = RedTeamFramework(llm_detection=True)

# Full audit across all attack categories
report = await framework.full_audit(target_agent)
print(f"Vulnerabilities found: {len(report.vulnerabilities)}")

# Quick scan on a specific category
scan = await framework.quick_scan(
    target_agent,
    categories=[AttackCategory.PROMPT_INJECTION, AttackCategory.JAILBREAK],
)
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
from core.meta import MetaAgent
from core.meta.debate import DebateOrchestrator

meta = MetaAgent()

# Analyze agent performance
analysis = await meta.analyze_agents()

# Multi-perspective debate
debate = DebateOrchestrator(llm_service=llm)
result = await debate.run_debate(
    query="Best caching strategy for this workload?",
    perspectives=["performance_expert", "cost_analyst", "reliability_eng"],
    rounds=3,
)
print(result.winner, result.agreements, result.disagreements)
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

# Record experience
await learner.record_experience(
    action=action,
    outcome=outcome,
    reward=feedback_score
)

# Extract lessons
lessons = await learner.extract_lessons()
```

---

### Evolution

**Module**: `core/learning/evolution.py`

Evolutionary improvement of prompts and strategies.

```python
from core.learning import EvolutionService

evolution = EvolutionService()

# Evaluate prompt population
scores = await evolution.evaluate_population(prompts)

# Evolve toward better performance
new_generation = await evolution.evolve(
    population=prompts,
    scores=scores,
    mutation_rate=0.1
)
```

---

### Auto Fine-Tuning

**Module**: `core/finetuning/`

Automatic fine-tuning based on feedback.

```python
from core.finetuning import AutoFineTuner

tuner = AutoFineTuner()

# Monitor performance
if await tuner.should_trigger():
    # Prepare dataset from experiences
    dataset = await tuner.prepare_dataset()
    
    # Start fine-tuning
    job = await tuner.start_finetuning(
        base_model="llama3.2",
        dataset=dataset
    )
```

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
from core.memory import MemoryManager

memory = MemoryManager()

# Unified access
context = await memory.get_context(session_id)
related = await memory.search_similar(query, k=5)
```

---

### Multi-Tenancy

**Module**: `core/context.py`

Data isolation between tenants.

```python
from core.context import tenant_context

# Set tenant for the request
async with tenant_context("tenant-123"):
    # All operations are isolated
    data = await repository.get_all()  # Only tenant data
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
from core.orchestration import Orchestrator
from core.reflection import ReflectionAgent
from core.guardrails import InputGuard, OutputGuard
from core.swarm import Colony

# Setup
orchestrator = Orchestrator()
reflection_agent = ReflectionAgent()
input_guard = InputGuard()
output_guard = OutputGuard()
colony = Colony()

async def handle_complex_request(query: str):
    # 1. Guardrails - validate input
    safe_input = await input_guard.process(query)
    
    # 2. Swarm - collaborative processing
    result = await colony.execute(safe_input.content)
    
    # 3. Reflection - self-evaluation
    evaluation = await reflection_agent.evaluate(result)
    
    # 4. Self-correction if needed
    if evaluation.score < 0.8:
        result = await reflection_agent.refine(result, evaluation.feedback)
    
    # 5. Guardrails - validate output
    safe_output = await output_guard.process(result)
    
    return safe_output
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
|                | Task Queue      | `core/task_queue/`  |
|                | Evaluation      | `core/evaluation/`  |
