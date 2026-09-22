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

Feedback on a generated answer (`extra="ignore"` — unexpected keys are
dropped rather than persisted):
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

Frozen dataclass. Rates are USD per 1M tokens:

| Field | Default | Meaning |
|---|---|---|
| `input_usd_per_million` | — | Standard (non-cached) input rate |
| `output_usd_per_million` | — | Output rate |
| `cache_read_usd_per_million` | `None` → `0.1 ×` input | Tokens served from a prompt-cache hit |
| `cache_write_usd_per_million` | `None` → `1.25 ×` input | Tokens newly written into the prompt cache |
| `batch_multiplier` | `0.5` | Applied to the **whole** estimate (input, output and both cache tiers) when a call is priced via the Message Batches API |

The two derived defaults are exposed as `effective_cache_read_usd_per_million` /
`effective_cache_write_usd_per_million`, so a model that does not publish its own
cache rates still prices correctly.

A row may also **override** a derivation by naming the rate, and one in
`DEFAULT_PRICING` does: `claude-fable-5-1` carries
`cache_read_usd_per_million=0.25` against a `$10` input rate — `0.025 ×`, not
the derived `0.1 ×`. The derivation is right for the Opus, Sonnet and Haiku
families and wrong here, and it billed **four times** the published rate, on
the term that dominates an agent loop (whose prefix is cached by design) and on
the number the tenant budget gate aborts against.

`claude-mythos-5-1` deliberately keeps the derived rate: its cache-read price
was left open at launch and is not published. Over-charging closes a budget
early, which an operator sees and can raise; under-charging overspends a cap
silently. When a rate is unknown, the derivation is the safe direction to be
wrong in.

`estimate(prompt_tokens, completion_tokens, *, cache_read_tokens=0,
cache_write_tokens=0, batch=False) -> float`. A negative token count raises
`ValueError`.

### Table & helpers

- `DEFAULT_PRICING` — a snapshot mapping common model ids to `ModelPrice`
  (Anthropic, OpenAI, Google, and zero-cost local models). Treat it as a
  default; override per deployment for negotiated rates.
- `PRICING_AS_OF` — the snapshot date of `DEFAULT_PRICING` (ISO string).
  Display this in dashboards/reports instead of hand-syncing a copy; refresh it
  together with the table. It marks a **full re-verification pass**, not the
  last edit: the Fable 5.1 cache-read correction above (that one rate
  re-verified on 2026-09-22) intentionally left the date where it was, since
  moving it would assert that every other vendor's row had been checked too.
- `UNKNOWN_PRICE` — a deliberately high fallback so missing entries are visible.
- `LOCAL_PROVIDERS` / `LOCAL_PRICE` / `qualified_model_id(provider, model)` —
  self-hosted inference is capacity-bound, not price-bound, so any
  `ollama/<model>` id prices at **zero without needing a table row**. This is
  not cosmetic: a bare local tag has no row, so it used to be priced through
  the unknown-model policy at `UNKNOWN_PRICE`'s punitive 100 $/M — money that
  was never spent, and enough to abort a budgeted run. Call
  `qualified_model_id` before costing a turn; it namespaces a model by its
  provider when that provider is local, and is idempotent. A local endpoint
  that fronts a paid model can still be priced with an explicit row for the
  qualified id, which wins.
- `is_priced(model_id, *, table=DEFAULT_PRICING)` — whether a lookup is backed
  by a real rate (a row, or a local model's known zero). What is left is a
  hosted model with no row, which is the case the unknown-model policy and its
  warning exist for.
- `get_price(model_id, *, table=DEFAULT_PRICING)` — a table row, else
  `LOCAL_PRICE` for a local id, else `UNKNOWN_PRICE`.
- `estimate_cost(model_id, input_tokens, output_tokens, *, cache_read_tokens=0,
  cache_write_tokens=0, batch=False, table=...)` — one-call USD estimate.

```python
from core.models.pricing import estimate_cost

usd = estimate_cost("claude-sonnet-5", 1200, 400)

# Forward the service layer's Usage fields straight through
usd = estimate_cost(
    "claude-sonnet-5",
    usage.input_tokens,
    usage.output_tokens,
    cache_read_tokens=usage.cache_read_tokens,
    cache_write_tokens=usage.cache_write_tokens,
)
```

!!! warning "`estimate_cost`'s first two parameters were renamed"
    They are now `input_tokens` / `output_tokens` (they were
    `prompt_tokens` / `completion_tokens`), matching the `Usage` fields the LLM
    service layer produces. **Positional callers are unaffected; keyword callers
    are not** — `estimate_cost(model, prompt_tokens=..., completion_tokens=...)`
    now raises `TypeError`. `ModelPrice.estimate` keeps the older parameter
    names.

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
  errors, where a second provider would double-spend, not recover) — plus two
  independent latency bounds, `stage_timeout_seconds` and
  `total_timeout_seconds`.

`FallbackChain.run(*args, **kwargs)` iterates providers, skipping any whose
breaker reports open, awaiting sync or async calls transparently, and returns
the first success as a `FallbackOutcome`.

```python
from core.models.fallback import FallbackChain, Provider

chain = FallbackChain(
    [
        Provider(name="anthropic", call=call_anthropic, is_open=breaker.is_open),
        Provider(name="openai", call=call_openai),
        Provider(name="local", call=call_ollama),
    ],
    stage_timeout_seconds=30.0,   # per stage; Provider.timeout_seconds overrides
    total_timeout_seconds=60.0,   # wall-clock for the whole chain
)

outcome = await chain.run(prompt="…")
print(outcome.provider, len(outcome.attempts))
```

### Two timeouts, two jobs

| Argument | Scope | Effect when exceeded |
| -------- | ----- | -------------------- |
| `stage_timeout_seconds` | One stage (a `Provider.timeout_seconds` wins over it) | That stage becomes a failed attempt; the chain moves on |
| `total_timeout_seconds` | The whole `run()`, measured on `time.monotonic()` | Remaining stages are **skipped**, recorded as `skipped=True` with `error="chain_deadline_exceeded"` |

Both default to `None` (unbounded). When a total budget is set, each stage's
effective timeout is `min(stage_timeout, time remaining)` — so no single stage
can outlive the chain — and a stage reached with zero budget left is never
started at all.

!!! warning "A per-stage timeout does not bound the chain"
    Without `total_timeout_seconds` the worst case is
    `stages × (SDK timeout × retry attempts + backoff)`. Three stages over a
    120s SDK timeout with retries is *many minutes* of one held HTTP request —
    long past the point the caller hung up and a reverse proxy (60s by default
    in nginx) returned a gateway timeout. Skipping the tail stages is the
    honest outcome: nobody is still listening for their answer.

Skipped-on-deadline stages are visible in the outcome, so an operator can tell
"every provider failed" from "we ran out of time":

```python
from core.models.fallback import AllProvidersFailedError

try:
    outcome = await chain.run(prompt="…")
except AllProvidersFailedError as exc:
    for attempt in exc.attempts:
        print(attempt.provider, attempt.skipped, attempt.error)
        # e.g. ("local", True, "chain_deadline_exceeded")
```

### Runtime wiring

The chain is wired into `LLMService.generate_response()` via
`core.services.llm.fallback_runtime`, and into
`generate_response_stream()` via `core.services.llm._stream_fallback` (which
can only switch provider before the first chunk reaches the caller): declare ordered stages with
`LLM_FALLBACK_CHAIN=provider:model,…` (empty disables it). Stages are cached
`LLMService` clones with per-provider credentials; open circuit breakers are
skipped; budget/deadline errors are fatal. The serving provider is recorded on
the span (`gen_ai.baselith.serving_provider`) and in GenAI metrics.

The runtime supplies both bounds from config: `stage_timeout_seconds` from
`LLM_FALLBACK_STAGE_TIMEOUT` (unset ⇒ unbounded per stage) and
`total_timeout_seconds` from `LLM_FALLBACK_TOTAL_TIMEOUT`, which **falls back
to `LLM_REQUEST_TIMEOUT` (default `120.0`)** rather than to "unbounded" — a
chain has no business outliving the request that started it. See
[LLM service](services.md) for details.
