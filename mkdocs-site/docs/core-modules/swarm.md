---
title: Swarm Intelligence
description: Multi-agent coordination with Colony, Auction, and Pheromones
---

The `core/swarm` module implements **collective intelligence** patterns, where multiple agents collaborate to solve complex problems.

## Swarm Intelligence Explained

Inspired by nature (ants, bees), the swarm coordinates specialized agents that:

- **Collaborate**: Share information and results
- **Specialize**: Each has specific skills
- **Self-Organize**: No centralized controller
- **Emerge**: Complex behaviors from simple rules

### When to Use Swarm

**Use Swarm when**:

- Complex tasks decomposable into specialized subtasks
- Diverse expertise is needed (research + analysis + writing)
- Parallelization significantly increases speed
- An agent failure should not block everything

**Use Single Agent when**:

- Simple or sequential task
- Coordination overhead > benefits
- Limited budget (swarm costs N times single agent)

---

## Structure

```text
core/swarm/
├── __init__.py
├── colony.py           # Agent colony
├── auction.py          # Task allocation via auction
├── pheromones.py       # Indirect communication
├── team_formation.py   # Dynamic team formation
└── types.py            # Common types
```

---

## Colony

A colony coordinates specialized agents:

```python
from core.swarm import Colony, SwarmAgent

# Create colony
colony = Colony()

# Register agents
colony.register(SwarmAgent(
    id="researcher",
    skills=["search", "summarize"],
    capacity=5
))

colony.register(SwarmAgent(
    id="analyst", 
    skills=["analyze", "compare"],
    capacity=3
))

colony.register(SwarmAgent(
    id="writer",
    skills=["write", "edit"],
    capacity=2
))

# Execute task
result = await colony.execute(
    task="Write a report on AI trends",
    strategy="auction"
)
```

---

## Coordination Strategies

### Auction

Agents "bid" for tasks:

```python
from core.swarm import AuctionCoordinator

coordinator = AuctionCoordinator()

# Available tasks
tasks = [
    {"id": "t1", "type": "research", "priority": "high"},
    {"id": "t2", "type": "analysis", "priority": "medium"},
]

# Allocation via auction
allocation = await coordinator.allocate(tasks, agents)
# {"t1": "researcher", "t2": "analyst"}
```

### Pheromones

Indirect communication via trails:

```python
from core.swarm import PheromoneTrail

trail = PheromoneTrail()

# Agent deposits trail
await trail.deposit(
    location="topic:AI",
    strength=0.8,
    agent_id="researcher"
)

# Other agents follow strong trails
strong_topics = await trail.get_strongest(k=5)
```

### Team Formation

Dynamic team formation:

```python
from core.swarm import TeamFormation

formation = TeamFormation()

# Form optimal team for complex task
team = await formation.form_team(
    required_skills=["research", "analyze", "write"],
    available_agents=agents,
    max_size=3
)
```

---

## SwarmAgent

```python
@dataclass
class SwarmAgent:
    id: str
    skills: list[str]
    capacity: int = 5  # Max parallel tasks
    status: str = "idle"  # idle, busy, offline
    
    async def execute(self, task: dict) -> dict:
        """Executes an assigned task."""
        ...
    
    def can_handle(self, task_type: str) -> bool:
        """Checks if can handle the task."""
        return task_type in self.skills
```

---

## Complete Workflow

```mermaid
sequenceDiagram
    participant C as Colony
    participant A as Auction
    participant R as Researcher
    participant An as Analyst  
    participant W as Writer
    
    C->>A: Allocates task "research"
    A->>R: Assigns (highest bid)
    R-->>C: Research result
    
    C->>A: Allocates task "analyze"
    A->>An: Assigns
    An-->>C: Analysis result
    
    C->>A: Allocates task "write"
    A->>W: Assigns
    W-->>C: Final report
```

---

## Real-World Use Cases

Practical examples of swarm in action.

### Use Case 1: Research Report Generation

**Task**: Generate complete report on a topic

**Swarm Design**:

```python
colony = Colony()

# Agent 1: Researcher
colony.register(SwarmAgent(
    id="researcher",
    skills=["web_search", "summarize"],
    capacity=10
))

# Agent 2: Analyst
colony.register(SwarmAgent(
    id="analyst",
    skills=["analyze", "compare", "critique"],
    capacity=5
))

# Agent 3: Writer
colony.register(SwarmAgent(
    id="writer",
    skills=["write", "edit", "format"],
    capacity=3
))

# Execute with auction
result = await colony.execute(
    task="Research report: AI trends 2024",
    strategy="auction"
)
```

**Flow**:

1. Researcher searches info online (parallel queries)
2. Analyst evaluates and compares sources
3. Writer generates structured report

**Benefits**: 3-5x faster than single sequential agent.

### Use Case 2: Code Review Swarm

**Task**: Complete PR review

```python
# Specialized agents
colony.register(SwarmAgent(id="security", skills=["security_audit"]))
colony.register(SwarmAgent(id="performance", skills=["perf_analysis"]))
colony.register(SwarmAgent(id="style", skills=["code_style", "best_practices"]))
colony.register(SwarmAgent(id="tests", skills=["test_coverage", "test_quality"]))

# Parallel review
result = await colony.execute(
    task=f"Review PR #{pr_number}",
    strategy="parallel"  # All in parallel
)

# Consolidate feedback
feedback = consolidate_reviews(result)
```

**Benefits**: More complete review, every aspect covered by a specialist.

### Use Case 3: Customer Support Triage

**Pheromone-Based Routing**:

```python
trail = PheromoneTrail()

# Agents leave trails on topics they handle well
@agent.on_success
async def leave_pheromone(topic, quality_score):
    await trail.deposit(
        location=f"topic:{topic}",
        strength=quality_score,
        agent_id=agent.id
    )

# Router assigns ticket following strong trails
async def route_ticket(ticket):
    topic = classify_topic(ticket)
    
    # Find agent with strongest pheromone
    best_agents = await trail.get_strongest(
        location=f"topic:{topic}",
        k=3
    )
    
    # Assign to agent with least load
    selected = min(best_agents, key=lambda a: a.current_load)
    await selected.handle_ticket(ticket)
```

**Benefits**: Self-learning routing, agents specialize automatically.

!!! tip "Pattern Selection"
    - **Auction**: Independent tasks, agents compete
    - **Pheromones**: Recurring patterns, learning over time
    - **Team Formation**: Complex task, requires tight coordination
