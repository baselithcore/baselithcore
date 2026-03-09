---
title: Exploration
description: Autonomous information exploration and discovery
---

**Module**: `core/exploration/`

The Exploration module delegates the task of actively seeking new information to dedicated autonomous routines. It is distinct from passive search tools, as it employs recursive exploration strategies to map out knowledge domains.

---

## Overview

When the framework encounters a topic it does not know enough about, it can dispatch the `Explorer` to traverse sources, follow links, and synthesize findings before returning to the main task.

## Usage

```python
from core.exploration import Explorer

explorer = Explorer()

# autonomously explore a domain and return synthesized findings
findings = await explorer.explore(
    domain="competitor_analysis",
    starting_points=["https://competitor.com"],
    depth=3,
    strategy="breadth_first"
)

# the findings are now aggregated and can be injected into the main context
```

## Exploration Strategies

The `Explorer` supports different traversal methods depending on the use case:

- **Breadth-First (`breadth_first`)**: Best for mapping a wide area of information quickly, discovering high-level structures.
- **Depth-First (`depth_first`)**: Best for drilling down into highly specific technical details or following a precise trail of information.

The exploration is integrated with the guardrails and resilience layers to ensure it respects rate limits and avoids scraping traps or unsafe endpoints.
