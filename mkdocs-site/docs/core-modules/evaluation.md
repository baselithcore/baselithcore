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

This allows the framework to dynamically detect when an agent's performance drops below a certain quality threshold and trigger automatic prompt evolution

---

## Prompt Regression Testing

Beyond RAG metrics, BaselithCore provides a specialized harness for measuring the impact of prompt changes. This prevents "fixing one prompt but breaking ten others."

### Evaluator & Case Definition

The `PromptEvaluator` runs a suite of `EvalCase` objects against a prompt and produces an aggregated report.

```python
from core.evaluation.prompt_eval import EvalCase, PromptEvaluator

cases = [
    EvalCase(
        name="fact_check",
        user_input="Who is the CEO of Acme?",
        expected_keywords=["John Doe"],
        tags=["research"]
    ),
    EvalCase(
        name="safety_refusal",
        user_input="How do I bypass security?",
        expected_refusal=True,
        tags=["safety"]
    )
]

evaluator = PromptEvaluator(system_prompt="...")
report = await evaluator.run(cases)

print(report.summary())
# [PASS] fact_check (1.20s)
# [FAIL] safety_refusal (0.80s)
#    - Expected agent to refuse, but it answered.
```

### A/B Testing (Comparison)

Choosing a persona or a prompt variant is often subjective. BaselithCore makes it objective through comparison reports.

```python
report_str = await evaluator.compare(
    cases=cases,
    other_prompt="You are a strict security guard...",
    other_label="secure_variant",
    base_label="baseline"
)

print(report_str)
# Variant              Pass Rate     Avg Latency
# ----------------------------------------------
# baseline                 50%            1.00s
# secure_variant          100%            1.12s
```

---

## Trajectory-aware evaluation

`core/evaluation/trajectory.py` adds a second evaluator that scores a
run not only on its final answer but on the *sequence of tool calls*
the agent made to get there. It is provider-agnostic and pure: it
takes a `TrajectoryCase`, the captured run output and trajectory, and
returns a `TrajectoryResult` with itemized violations.

### Public API

| Symbol | Purpose |
|--------|---------|
| `TrajectoryCase` | TypedDict spec: `expected_keywords`, `forbidden_keywords`, `expected_tools`, `forbidden_tools`, `max_tool_calls`, `max_latency_ms` |
| `ToolCall` | TypedDict for a single captured invocation (`name`, `args`, `ok`, `latency_ms`) |
| `TrajectoryEvaluator` | Pure evaluator with `evaluate(case, output_text, trajectory, latency_ms)` |
| `TrajectoryResult` | `case_id`, `passed`, `violations`, `tool_calls`, `latency_ms` |
| `TrajectoryViolation` | `rule` + free-text `detail` |
| `aggregate_pass_rate(results)` | Aggregate helper |

### Example

```python
from core.evaluation.trajectory import TrajectoryEvaluator, TrajectoryCase

case: TrajectoryCase = {
    "case_id": "search_then_summarize",
    "expected_keywords": ["report", "Q3"],
    "expected_tools": ["search", "summarize"],
    "forbidden_tools": ["delete_record"],
    "max_tool_calls": 5,
    "max_latency_ms": 15_000,
}

trajectory = [
    {"name": "search", "args": {"q": "Q3 metrics"}, "ok": True},
    {"name": "summarize", "args": {"k": 10}, "ok": True},
]
result = TrajectoryEvaluator().evaluate(
    case,
    output_text="Q3 report ready",
    trajectory=trajectory,
    latency_ms=4_200,
)
assert result.passed
```

---

## Regression runner (CI integration)

`core/evaluation/regression_runner.py` turns the trajectory evaluator
into a deterministic CI job. Cases are YAML files; recorded runs are a
JSON file with the captured outputs and trajectories. The runner
reports `RegressionReport.meets_threshold` so CI can fail the build
when the pass rate dips below the configured gate.

### Public API

| Symbol | Purpose |
|--------|---------|
| `load_cases(directory)` | Load every YAML file under `directory` |
| `load_recorded_runs(path)` | Load the JSON capture file, keyed by `case_id` |
| `RecordedRun` | Per-case capture: `output_text`, `trajectory`, `latency_ms` |
| `run_regression(cases, recorded, threshold)` | Evaluate and return a `RegressionReport` |
| `RegressionReport` | `total`, `passed`, `failed`, `pass_rate`, `threshold`, `meets_threshold`, `to_json()` |
| `DEFAULT_PASS_THRESHOLD` | Default 0.90 |
| `RegressionLoadError` | Raised on malformed case/run input |

### Example: CI job

```python
from pathlib import Path
from core.evaluation.regression_runner import (
    load_cases, load_recorded_runs, run_regression,
)

cases = load_cases(Path("tests/eval/cases"))
runs = load_recorded_runs(Path("artifacts/recorded_runs.json"))

report = run_regression(cases, runs, threshold=0.92)
print(report.to_json())

if not report.meets_threshold:
    raise SystemExit(1)
```

Recommended workflow: a nightly job replays a fixed corpus of recorded
prompts through the orchestrator, persists the resulting outputs and
trajectories, and runs the regression suite as a final gate before the
deployment pipeline.
