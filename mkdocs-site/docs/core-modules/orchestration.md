---
title: Orchestration
description: Orchestrator, Intent Classifier, and Flow Router
---

The `core/orchestration` module manages request routing to appropriate plugins.

## Module Structure

```text
core/orchestration/
├── __init__.py              # Public exports
├── orchestrator.py          # Main Orchestrator
├── intent_classifier.py     # Intent classification
├── router.py                # Plugin routing
├── protocols.py             # FlowHandler interfaces
└── handlers/                # Built-in handlers
    ├── default.py           # Fallback handler
    └── ...
```

---

## Orchestrator

The central component coordinating request processing:

```python
from core.orchestration import Orchestrator

orchestrator = Orchestrator()

# Synchronous handling
response = await orchestrator.handle_request(
    query="What's the weather in Rome?",
    session_id="user-123",
    stream=False
)

# Streaming handling
async for chunk in orchestrator.handle_stream(
    query="Analyze this document",
    session_id="user-123"
):
    print(chunk, end="")
```

### Internal Flow

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant M as Memory
    participant I as IntentClassifier
    participant R as PluginRegistry
    participant H as FlowHandler

    O->>M: get_context(session_id)
    M-->>O: Context
    O->>I: classify(query, context)
    I-->>O: Intent
    O->>R: get_handler(intent)
    R-->>O: Handler
    O->>H: handle(query, context)
    H-->>O: Response
    O->>M: update_context()
```

### API Reference

```python
class Orchestrator:
    async def handle_request(
        self,
        query: str,
        session_id: str,
        stream: bool = True,
        metadata: dict | None = None
    ) -> str:
        """
        Handles a complete request.

        Args:
            query: User's query
            session_id: Session ID for context
            stream: If True, uses streaming handler
            metadata: Optional additional data

        Returns:
            Complete response as string
        """

    async def handle_stream(
        self,
        query: str,
        session_id: str,
        metadata: dict | None = None
    ) -> AsyncGenerator[str, None]:
        """
        Handles a streaming request.

        Yields:
            Response chunks
        """
```

---

## Intent Classifier

Determines which handler should manage the request:

```python
from core.orchestration import IntentClassifier

classifier = IntentClassifier()

result = await classifier.classify(
    query="Analyze market trends",
    context=context
)

print(result.intent)      # "reasoning"
print(result.confidence)  # 0.92
print(result.source)      # "pattern" | "llm"
```

### Pattern Matching

First fast phase based on patterns:

```python
# Plugins register patterns
patterns = [
    {
        "intent": "weather",
        "patterns": ["meteo", "weather", "temperature"],
        "priority": 100
    },
    {
        "intent": "reasoning",
        "patterns": ["analyze", "compare", "reason"],
        "priority": 90
    }
]
```

### LLM Fallback

If no pattern matches, uses LLM:

```python
# LLM-based classification
prompt = f"""
Classify the following query into one of these intents:
- weather: weather questions
- reasoning: complex analysis
- chat: generic conversation

Query: {query}
Intent:
"""

intent = await llm.generate(prompt)
```

### Priority Resolution

```mermaid
flowchart TD
    Query --> Patterns[Check Patterns]
    Patterns --> |Match| Priority{Compare Priority}
    Patterns --> |No Match| LLM[LLM Classification]
    Priority --> |Highest| Handler[Selected]
    LLM --> |confidence > 0.7| Handler
    LLM --> |confidence < 0.7| Default[Default Handler]
```

---

## Flow Router

Maps intents to handlers:

```python
from core.orchestration import FlowRouter

router = FlowRouter()

# Register handler
router.register("weather", WeatherHandler)
router.register("reasoning", ReasoningHandler)

# Resolve
handler = router.resolve("weather")
```

---

## Flow Handler Protocol

Plugins implement this protocol:

```python
from core.orchestration.protocols import FlowHandler

class FlowHandler(Protocol):
    async def handle(
        self,
        query: str,
        context: dict
    ) -> str:
        """Synchronous handler."""
        ...

    async def handle_stream(
        self,
        query: str,
        context: dict
    ) -> AsyncGenerator[str, None]:
        """Streaming handler."""
        ...
```

### Implementation

```python
class MyHandler(FlowHandler):
    def __init__(self, plugin):
        self.llm = resolve(LLMServiceProtocol)

    async def handle(self, query: str, context: dict) -> str:
        # Synchronous logic
        response = await self.llm.generate(query)
        return response.text

    async def handle_stream(
        self,
        query: str,
        context: dict
    ) -> AsyncGenerator[str, None]:
        # Streaming logic
        async for chunk in self.llm.stream(query):
            yield chunk
```

---

## Plugin Registration

Plugins register handlers via `get_flow_handlers()`:

```python
# plugins/my-plugin/plugin.py
class MyPlugin(Plugin):
    def get_flow_handlers(self) -> dict:
        return {
            "my_intent": {
                "sync": MySyncHandler,
                "stream": MyStreamHandler,
            }
        }

    def get_intent_patterns(self) -> list:
        return [
            {
                "intent": "my_intent",
                "patterns": ["keyword1", "keyword2"],
                "priority": 100
            }
        ]
```

---

## Context Management

The orchestrator manages conversation context:

```python
@dataclass
class ConversationContext:
    session_id: str
    tenant_id: str | None
    user_id: str | None
    messages: list[Message]
    compressed_memory: str | None
    metadata: dict
```

### Context Propagation

```python
async def handle_request(self, query: str, session_id: str):
    # 1. Load context
    context = await self.memory.get_context(session_id)

    # 2. Classify with context
    intent = await self.classifier.classify(query, context)

    # 3. Execute with context
    handler = self.router.resolve(intent)
    response = await handler.handle(query, context.to_dict())

    # 4. Update context
    await self.memory.add_message(session_id, "user", query)
    await self.memory.add_message(session_id, "assistant", response)

    return response
```

---

## Event Emission

The orchestrator emits events for observability:

```python
from core.events import get_event_bus, EventNames

async def handle_request(self, ...):
    start = time.time()

    # Emit start
    await self.bus.emit(EventNames.FLOW_STARTED, {
        "intent": intent,
        "session_id": session_id
    })

    try:
        result = await handler.handle(query, context)

        # Emit completion
        await self.bus.emit(EventNames.FLOW_COMPLETED, {
            "intent": intent,
            "duration_ms": (time.time() - start) * 1000,
            "success": True
        })

        return result

    except Exception as e:
        await self.bus.emit(EventNames.FLOW_COMPLETED, {
            "intent": intent,
            "success": False,
            "error": str(e)
        })
        raise
```

---

## Configuration

```python
from core.config import get_orchestration_config

config = get_orchestration_config()

print(config.default_intent)        # "chat"
print(config.classifier_threshold)  # 0.7
print(config.max_context_messages)  # 20
```

```env title=".env"
ORCHESTRATION_DEFAULT_INTENT=chat
ORCHESTRATION_CLASSIFIER_THRESHOLD=0.7
ORCHESTRATION_MAX_CONTEXT_MESSAGES=20
```

---

## Runtime guardrails

The orchestrator carries three optional, request-scoped guardrails that
fire before any tool is dispatched and any LLM call leaves the process.

### `LoopBudget` — iteration + cost cap

`core/orchestration/limits.py` enforces hard caps so a runaway loop
cannot burn budget. A fresh `LoopBudget` is instantiated per request
by `ExecutionMixin.process` and exposed as `context["loop_budget"]`.

| Symbol | Purpose |
|--------|---------|
| `LoopLimits` | Static caps (`max_iterations`, `max_tool_calls`, `budget_usd`) |
| `LoopBudget` | Mutable per-request tracker with `tick()`, `record_tool_call()`, `charge(cost)` |
| `LoopBudgetSnapshot` | Immutable snapshot returned by `snapshot()` |
| `BudgetExceededError` | Raised when any cap is breached |

Defaults: 25 iterations, 50 tool calls, USD 0.50. Override at
construction:

```python
from core.orchestration import Orchestrator
from core.orchestration.limits import LoopLimits

orchestrator = Orchestrator(
    loop_limits=LoopLimits(
        max_iterations=10,
        max_tool_calls=20,
        budget_usd=0.10,
    ),
)
```

Handlers downstream call the budget directly:

```python
budget = context["loop_budget"]
budget.tick()                          # before each agentic step
budget.record_tool_call()              # before every tool dispatch
budget.charge(0.0008)                  # after each LLM completion
```

A breach raises `BudgetExceededError`, which `ExecutionMixin` catches
and converts into a structured failure reply with `budget_exceeded` and
a snapshot of the state at the breach.

### `AgentContract` — declarative spec

`core/orchestration/contract.py` loads a YAML file describing the
agent's identity, allowed/forbidden tools, output contract, and quality
gates. When a contract is wired into the orchestrator, the runtime
`ContractValidator` is exposed at `context["contract_validator"]`.

```yaml
# agent.yaml
name: example-agent
version: 1.0.0
identity: research assistant for internal teams
capabilities:
  allowed_tools: [search, read, summarize]
  must_not: [delete, rm_rf, transfer_funds]
output_contract:
  format: json
  required_fields: [answer, sources]
quality_gates:
  min_eval_pass_rate: 0.92
  max_cost_usd: 0.10
```

```python
from core.orchestration import Orchestrator
from core.orchestration.contract import load_contract

contract = load_contract("agent.yaml")
orchestrator = Orchestrator(agent_contract=contract)
```

Handlers gate tool dispatch with `validator.check_tool_call(name)` and
output shape with `validator.check_output(payload)`. Both raise
`ContractViolationError` on failure.

### `AutonomyPolicy` — three-tier spectrum

`core/orchestration/autonomy.py` provides a coarse-grained policy that
governs which tool categories require human approval.

| Level | Read-only | Mutating | Destructive | External side-effect |
|-------|-----------|----------|-------------|----------------------|
| `SUPERVISED` | auto | approval | approval | approval |
| `SEMI_AUTONOMOUS` | auto | auto | approval | approval |
| `FULLY_AUTONOMOUS` | auto | auto | auto | auto |

`AutonomyUpgradeGate` decides whether an operator may advance the
deployment to the next level. Upgrade is blocked until evaluation pass
rate, red-team pass rate, and successful-run count all clear their
thresholds (default 0.90 → 0.98).

```python
from core.orchestration.autonomy import (
    AutonomyLevel, AutonomyPolicy, AutonomyUpgradeGate,
)

policy = AutonomyPolicy(level=AutonomyLevel.SEMI_AUTONOMOUS)
orchestrator = Orchestrator(autonomy_policy=policy)

gate = AutonomyUpgradeGate(
    eval_pass_rate=0.97,
    red_team_pass_rate=1.0,
    successful_runs=120,
)
allowed, reasons = gate.can_upgrade_to(AutonomyLevel.FULLY_AUTONOMOUS)
```

### `TaskClassifier` — short-circuit deterministic tasks

`core/orchestration/task_classifier.py` is a lightweight heuristic that
returns one of `AGENTIC` / `DETERMINISTIC` / `AMBIGUOUS` for a task
description. It is conservative: when in doubt the recommendation is
`AGENTIC`. Use it at the front of the orchestrator to skip the loop on
clearly deterministic requests.

```python
from core.orchestration.task_classifier import (
    RoutingRecommendation, TaskClassifier,
)

result = TaskClassifier().classify(query)
if result.recommendation is RoutingRecommendation.DETERMINISTIC:
    return run_deterministic_pipeline(query)
```

Each result carries the extracted signal (`word_count`, `has_conditional`,
agentic/deterministic hit counts) and a short rationale string for
audit logging.
