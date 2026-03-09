---
title: Meta-Agent & Debate
description: Agent coordinating other agents and running multi-perspective debates
---

**Module**: `core/meta/`

The Meta-Agent serves as a higher-level coordinator that observes, evaluates, and optimizes the performance of other agents. It can analyze agent behaviors and facilitate complex resolution strategies, such as multi-perspective debates.

---

## Module Structure

```text
core/meta/
├── __init__.py           # Public exports
├── agent.py              # MetaAgent core
└── debate.py             # DebateOrchestrator
```

---

## Multi-Perspective Debate

A core feature of the Meta module is the `DebateOrchestrator`, which forces multiple AI perspectives to argue opposing views on a complex topic before synthesizing a conclusion.

### Example Usage

```python
from core.meta import MetaAgent
from core.meta.debate import DebateOrchestrator

meta = MetaAgent()

# Optionally analyze existing agent performance across the platform
analysis = await meta.analyze_agents()

# Initiate a multi-perspective debate on a complex query
debate = DebateOrchestrator(llm_service=llm)
result = await debate.run_debate(
    query="What is the best caching architecture for a highly distributed multi-tenant system?",
    perspectives=["performance_expert", "cost_analyst", "reliability_engineer"],
    rounds=3,
)

print(f"Winner perspective: {result.winner}")
print(f"Key Agreements: {result.agreements}")
print(f"Remaining Disagreements: {result.disagreements}")
```

## Debate Internals

Behind the scenes, the debate orchestrator performs sophisticated extraction:

- `_find_agreements_disagreements()`: Uses LLM semantic analysis to extract structured agreements and disagreements from the free-text perspectives of the simulated participants. Fails over to a keyword heuristic if necessary.
- `_determine_winner()`: Scores perspectives not just by assertiveness, but by evaluating confidence, logic, and the frequency with which opposing perspectives concede points or cite the winning argument across the debate rounds.
