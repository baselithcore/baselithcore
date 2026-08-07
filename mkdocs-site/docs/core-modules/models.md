---
title: Domain Models & Portability Primitives
description: Chat/domain Pydantic models, pricing, routing, and fallback
---

The `core/models` module holds the framework's domain Pydantic models (chat and
document/search types) plus the **provider-portability primitives**: a pricing
table, a cost-aware model router, and a provider fallback chain. These
primitives are provider-agnostic, so swapping LLM vendors does not touch
business logic.

## Overview

```text
core/models/
├── __init__.py    # Re-exports domain models
├── chat.py        # ChatRequest, ChatResponse, FeedbackRequest, FeedbackDocumentReference
├── domain.py      # Document, SearchResult
├── pricing.py     # ModelPrice, DEFAULT_PRICING, get_price, estimate_cost
├── routing.py     # ModelRouter, RoutingPolicy, TaskCategory, Complexity, RoutingDecision
├── routing_stats.py # RoutingScoreboard, LearnedModelRouter, RouteStats
└── fallback.py    # FallbackChain, Provider, FallbackOutcome, AllProvidersFailedError
```

The package `__init__` re-exports the domain models:

```python
from core.models import (
    ChatRequest, ChatResponse,
    FeedbackRequest, FeedbackDocumentReference,
    Document, SearchResult,
)
```

The portability primitives are imported from their submodules
(`core.models.pricing`, `core.models.routing`, `core.models.fallback`).

---

## Chat models

`core/models/chat.py`.

### `ChatRequest`

Input to the agent. Rejects unknown fields (`extra="forbid"`).

| Field | Type | Notes |
| ----- | ---- | ----- |
| `query` | `str` | Required, 1–8000 chars |
| `conversation_id` | `str \| None` | Conversation/session id |
| `stream` | `bool \| None` | Compatibility flag; use `/chat/stream` |
| `rag_only` | `bool` | Restrict to retrieval-only answers |
| `kb_label` | `str \| None` | Knowledge-base label filter |
| `tenant_id` | `str \| None` | Tenant override |
| `max_response_tokens` | `int \| None` | Upper bound, 1–16000 |

### `ChatResponse`

Agent output (`extra="allow"`): `answer` (str), optional `metadata`, `sources`
(list of dicts), and `conversation_id`.

### `FeedbackRequest`

Feedback on a generated answer (`extra="allow"` for legacy payloads):
`query` (1–8000), `answer` (1–32000), `feedback` (`positive`|`negative`),
optional `conversation_id`, `sources`, and `comment`.

### `FeedbackDocumentReference`

A source reference cited in an answer (`extra="forbid"`): `document_id`,
`title`, `path`, `url`, `origin`, `source_type` (`path`|`url`), and `score`.

---

## Domain models

`core/models/domain.py`.

### `Document`

A document in the system (`extra="allow"`): `content` (str), `id` (str,
defaults empty), `metadata` (dict), and optional `vector` (list of floats). A
model validator backfills `id` from `metadata["id"]` when `id` is empty.

### `SearchResult`

A vector-store hit (`extra="ignore"`): `document` (a `Document`) and `score`
(float).

---

## Pricing

`core/models/pricing.py` — an LLM pricing table for cost-aware decisions.
Prices are USD per 1M tokens, kept as a data table so a refresh is a single PR.

### `ModelPrice`

Frozen dataclass with `input_usd_per_million` and `output_usd_per_million`, plus
`estimate(prompt_tokens, completion_tokens) -> float`.

### Table & helpers

- `DEFAULT_PRICING` — a snapshot mapping common model ids to `ModelPrice`
  (Anthropic, OpenAI, Google, and zero-cost local models). Treat it as a
  default; override per deployment for negotiated rates.
- `PRICING_AS_OF` — the snapshot date of `DEFAULT_PRICING` (ISO string).
  Display this in dashboards/reports instead of hand-syncing a copy; refresh it
  together with the table.
- `UNKNOWN_PRICE` — a deliberately high fallback so missing entries are visible.
- `get_price(model_id, *, table=DEFAULT_PRICING)` — returns the `ModelPrice` or
  `UNKNOWN_PRICE`.
- `estimate_cost(model_id, prompt_tokens, completion_tokens, *, table=...)` —
  one-call USD estimate.

```python
from core.models.pricing import estimate_cost

usd = estimate_cost("claude-sonnet-4-6", prompt_tokens=1200, completion_tokens=400)
```

---

## Model routing

`core/models/routing.py` — picks a model that fits the task instead of always
using the flagship. Policy-driven and provider-agnostic.

### Concepts

- **`TaskCategory`**: `PLANNING`, `REASONING`, `EXECUTION`, `CLASSIFICATION`,
  `SUMMARIZATION`, `EMBEDDING`.
- **`Complexity`**: `SIMPLE`, `MEDIUM`, `COMPLEX` — breaks ties within a
  category.
- **`RoutingDecision`** (frozen): the chosen `model_id` plus rationale (`rule`,
  `category`, `complexity`).
- **`RoutingPolicy`**: maps categories to a `primary` model and optional
  `complexity_upgrade` overrides; `select()` applies an upgrade if present,
  otherwise the primary. Defaults are production-safe (planning/reasoning →
  flagship; execution → mid; classification/summarization → small).
- **`ModelRouter`**: a thin facade over a `RoutingPolicy`.

```python
from core.models.routing import ModelRouter, TaskCategory, Complexity

router = ModelRouter()
decision = router.select(TaskCategory.EXECUTION, Complexity.COMPLEX)
print(decision.model_id, decision.rule)  # upgraded model, "complexity_upgrade"
```

### Learned routing (opt-in)

A static table encodes what someone believed at design time; production
traffic knows better. `core/models/routing_stats.py` turns the table into a
scoreboard fed by observed outcomes.

```python
from core.models.routing import TaskCategory
from core.models.routing_stats import LearnedModelRouter, RoutingScoreboard

board = RoutingScoreboard(min_samples=20, margin=0.05)
board.record(TaskCategory.SUMMARIZATION, "claude-haiku-4-5",
             success=True, cost_usd=0.0004, latency_ms=310)

router = LearnedModelRouter(scoreboard=board)
decision = router.select(TaskCategory.SUMMARIZATION)
decision.rule  # "primary" | "complexity_upgrade" | "learned_override"
```

Three guards keep the scoreboard from being worse than the static default:

- **Minimum samples** — one lucky run cannot get an architecture review
  downgraded to a small model.
- **Margin** — a challenger must beat the incumbent by a real gap, not by
  measurement noise.
- **Allowed set** — the scoreboard may only prefer a model the policy already
  lists, so it can never route to something the deployment never vetted.

Every override is reported as `rule="learned_override"`: an audit can always
tell a policy decision from a learned one. Without a scoreboard,
`LearnedModelRouter` behaves exactly like `ModelRouter`.

!!! warning
    The scoreboard trusts whatever `success` flag it is handed. A lenient
    verifier produces a lenient routing table — define the acceptance
    criterion with the same rigor as an eval case.

### Runtime wiring

The router is wired into `LLMService` via `core.services.llm.model_routing`:
enable with `LLM_ROUTING_ENABLED=true` and (optionally) override the policy
with `LLM_ROUTING_POLICY` — a JSON object mapping category values to model ids
(e.g. `{"planning": "gpt-4o", "classification": "gpt-4o-mini"}`). Callers pass
`task_category="classification"` (etc.) to `generate_response()`/`generate()`;
model precedence is **pinned > per-call > routed > config default**. See
[LLM service](services.md) for details.

---

## Fallback chain

`core/models/fallback.py` — try a primary provider, then secondaries on
failure. Provider-agnostic and composable with circuit breakers.

### Concepts

- **`Provider`** (frozen, generic): a `name`, an async-or-sync `call`, and an
  optional `is_open` breaker check.
- **`ProviderAttempt`** (frozen): per-attempt record (`provider`, `succeeded`,
  `error`, `skipped`).
- **`FallbackOutcome`** (frozen, generic): the successful `result`, the winning
  `provider`, and the full `attempts` trail.
- **`AllProvidersFailedError`**: raised when every provider failed/was skipped;
  carries `.attempts`.
- **`FallbackChain`**: ordered list of providers; requires at least one and
  rejects duplicate names. Accepts `fatal_exceptions` — exception types that
  re-raise immediately instead of falling through (used for budget/deadline
  errors, where a second provider would double-spend, not recover).

`FallbackChain.run(*args, **kwargs)` iterates providers, skipping any whose
breaker reports open, awaiting sync or async calls transparently, and returns
the first success as a `FallbackOutcome`.

```python
from core.models.fallback import FallbackChain, Provider

chain = FallbackChain([
    Provider(name="anthropic", call=call_anthropic, is_open=breaker.is_open),
    Provider(name="openai", call=call_openai),
    Provider(name="local", call=call_ollama),
])

outcome = await chain.run(prompt="…")
print(outcome.provider, len(outcome.attempts))
```

### Runtime wiring

The chain is wired into `LLMService.generate_response()` via
`core.services.llm.fallback_runtime`: declare ordered stages with
`LLM_FALLBACK_CHAIN=provider:model,…` (empty disables it). Stages are cached
`LLMService` clones with per-provider credentials; open circuit breakers are
skipped; budget/deadline errors are fatal. The serving provider is recorded on
the span (`gen_ai.baselith.serving_provider`) and in GenAI metrics. See
[LLM service](services.md) for details.
