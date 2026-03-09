---
title: Evaluation
description: LLM response quality evaluation via RAG metrics
---

**Module**: `core/services/evaluation/`

The Evaluation module provides LLM-as-a-Judge capabilities, specifically tailored for Retrieval-Augmented Generation (RAG) metrics. It integrates with the event system to enable automated continuous optimization of agentic performance.

---

## Module Structure

```text
core/services/evaluation/
├── __init__.py           # Service factory
├── service.py            # Main EvaluationService
├── metrics/              # RAG metric implementations
│   ├── faithfulness.py   # Grounding check
│   ├── relevancy.py      # Query matching
│   └── ...
└── protocols.py          # Interface definitions
```

---

## Evaluation Metrics

The service evaluates responses using 4 fundamental RAG metrics:

| Metric                 | When Applicable        | Description                                          |
| ---------------------- | ---------------------- | ---------------------------------------------------- |
| `faithfulness`         | Always                 | How well the answer is grounded in retrieved context |
| `answer_relevancy`     | Always                 | How relevant the answer is to the original query     |
| `contextual_precision` | With `expected_output` | Ranking quality of retrieved documents               |
| `contextual_recall`    | With `expected_output` | Coverage of ground-truth in retrieved context        |

## Usage

### Basic Evaluation Request

```python
from core.services.evaluation.service import EvaluationService

evaluation = EvaluationService()

# Evaluate a RAG response
metrics = await evaluation.evaluate_rag_response(
    query="How does the caching work?",
    response="The system uses a Redis-based cache with TTL.",
    retrieved_contexts=[
        "Cache implementation uses Redis Enterprise.",
        "TTL is set to 3600 seconds by default."
    ],
    expected_output="Redis cache with a 1 hour TTL.",  # enables precision/recall
)

print(f"Faithfulness: {metrics['faithfulness']}")
print(f"Precision:    {metrics['contextual_precision']}")
print(f"Recall:       {metrics['contextual_recall']}")
```

---

## Integration with Optimization Loop

The evaluation service plays a critical role in the system's autonomous improvement capabilities. When an evaluation completes, it emits an event that the optimization system can intercept:

**Event flow**: `FLOW_COMPLETED` → `EvaluationService` → `EVALUATION_COMPLETED` → `OptimizationLoop` → `auto_tune()` → `OPTIMIZATION_COMPLETED`

This allows the framework to dynamically detect when an agent's performance drops below a certain quality threshold and trigger automatic prompt evolution.
