---
title: Exploration
description: Autonomous information exploration and hypothesis generation
---

**Module**: `core/exploration/`

The Exploration module actively seeks new information across pluggable knowledge
sources, identifies knowledge gaps, and generates testable hypotheses. It is
distinct from passive search tools: it expands queries, aggregates findings, and
scores confidence.

---

## Public API

`core.exploration` exports two pillars:

```python
from core.exploration import (
    ProactiveExplorer, ExplorationResult,   # information gathering
    HypothesisGenerator, Hypothesis,        # hypothesis generation
)
```

There is **no** `Explorer` class — the explorer pillar is `ProactiveExplorer`.

---

## Proactive Exploration

`ProactiveExplorer` searches across `KnowledgeSource` implementations (a
`Protocol` exposing async `search(query)` and `get_related(topic)`), expands the
topic into multiple queries, aggregates results, and returns an
`ExplorationResult`.

```python
from core.exploration import ProactiveExplorer

explorer = ProactiveExplorer(sources=[my_source])  # optional LLM for query expansion

result = await explorer.explore(
    topic="vector database tradeoffs",
    depth=1,         # how deep to follow related topics
    max_results=10,
)

print(result.findings)          # List[str]
print(result.sources)           # List[str]
print(result.confidence)        # float, 0..1
print(result.gaps_identified)   # List[str] of knowledge gaps
```

`explore()` takes only `topic`, `depth` (default `1`), and `max_results`
(default `10`). There are no `domain`, `starting_points`, or `strategy`
parameters and no breadth/depth-first strategy modes.

`ExplorationResult` fields: `query`, `findings`, `sources`, `confidence`,
`gaps_identified`.

---

## Hypothesis Generation

`HypothesisGenerator` turns a context plus known facts and unknowns into a list
of `Hypothesis` objects (LLM-backed when a service is available, otherwise a
simple heuristic fallback).

```python
from core.exploration import HypothesisGenerator

generator = HypothesisGenerator()  # optional llm_service

hypotheses = await generator.generate(
    context="Latency spiked after the last deploy",
    known_facts=["CPU is normal", "DB connections stable"],
    unknowns=["Which service regressed?"],
    max_hypotheses=3,
)

for h in hypotheses:
    print(h.statement, h.confidence.value)  # ConfidenceLevel: high/medium/low/speculative
    print("testable:", h.is_testable)
```

`Hypothesis` fields: `statement`, `confidence` (a `ConfidenceLevel` enum:
`HIGH` / `MEDIUM` / `LOW` / `SPECULATIVE`), `supporting_evidence`,
`contradicting_evidence`, `assumptions`. The `.is_testable` property is `True`
when the hypothesis has at least one assumption.
