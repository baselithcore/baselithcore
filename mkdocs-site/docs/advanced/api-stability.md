---
title: API Stability Tiers
description: What each part of the framework promises its callers, and what changing it costs
---

BaselithCore is 66 packages wide. Some of them back the HTTP API and the
plugin contract; others are research surface published so it can be tried.
Before tiers existed the [versioning policy](versioning-and-deprecation.md)
gave all of them the same promise — the public API surface gate treats every
one of 1 329 exported symbols as breaking to remove — so retiring an
experiment cost a MAJOR release. A framework that cannot retire an experiment
stops publishing them.

Tiers say out loud what was previously implied.

## The four tiers

| Tier | Promise | Removing a symbol costs |
| --- | --- | --- |
| `stable` | The shape is settled. Build on it. | MAJOR, after a deprecation cycle |
| `beta` | Production-ready; the shape may still move. | MINOR, after a deprecation cycle |
| `experimental` | Research surface. No compatibility promise. | any MINOR |
| `deprecated` | Compatibility shim, already superseded. | MAJOR, at the announced end of the overlap |

The current split:

| Tier | Packages |
| --- | --- |
| `stable` | `baselith`, `baselith.core.agent`, `baselith.core.api`, `baselith.core.auth`, `baselith.core.cache`, `baselith.core.config`, `baselith.core.db`, `baselith.core.events`, `baselith.core.guardrails`, `baselith.core.lifecycle`, `baselith.core.middleware`, `baselith.core.models`, `baselith.core.observability`, `baselith.core.plugins`, `baselith.core.resilience`, `baselith.core.security` |
| `beta` | 29 packages — the production infrastructure: `baselith.core.memory`, `baselith.core.orchestration`, `baselith.core.mcp`, `baselith.core.task_queue`, `baselith.core.services`, … |
| `experimental` | 19 packages — the cognitive and research surface: `baselith.core.swarm`, `baselith.core.meta`, `baselith.core.planning`, `baselith.core.skill_evolution`, … |
| `deprecated` | `baselith.core.doc_sources`, `baselith.core.routers`, `baselith.core.scraper` — the domain shims frozen by the Sacred Core rule |

`python scripts/check_public_api.py --list` prints every package with its tier
and symbol count.

## Where a tier is declared

In one table, `PACKAGE_STABILITY` in
[`baselith/core/stability.py`](https://github.com/baselithcore/baselithcore/blob/main/baselith/core/stability.py)
— not as a literal in each `__init__.py`. A table can be checked for
*completeness*, and it is: the gate asserts its keys are exactly the packages
that exist, so a new package cannot arrive unclassified and an entry cannot
outlive the package it describes. Sixty scattered literals give no such
guarantee.

```python
from baselith.core.stability import stability_of

stability_of("baselith.core.memory.hybrid_search")  # -> Stability.BETA
```

A tier is a property of the package. One symbol can carry its own:

```python
from baselith.core.stability import stable

@stable
def load_manifest(path: str) -> Manifest:
    """Depended on by every plugin author, inside an otherwise beta package."""
```

## The facade overrides everything

A symbol re-exported by [`baselith`](../api/python.md) is `stable` whatever
its package's tier. The facade is the contract; `baselith.core.*` is where
the implementation happens to live today. `baselith.LoopBudget` is stable while
`baselith.core.orchestration`, the package defining it, stays `beta`.

## One namespace since 1.0

Since 1.0 every package lives under the single top-level `baselith` package:
the tier keys are `baselith.core.*`. The `baselith` prefix does not pass the
facade's promise down: `stability_of("baselith.core.x")` resolves through the
`baselith.core.*` entries only, never through the facade's `stable`, so a
module outside the table resolves to `experimental` whatever its import path.
The bundled plugins (`baselith.plugins.*`) are plugins, not framework API, and
resolve the same way.

The old top-level names `core` and `plugins` are not part of the API at all.
The optional `baselith-core-compat` distribution aliases them during a
migration; see [Migrating to 1.0](migrating-to-1-0.md).

## Tiers only ratchet

A package may move **toward** stability — `experimental` → `beta` → `stable` —
or to `deprecated` from anywhere. It may never move back down the ladder: the
weaker promise was already published, so withdrawing the stronger one is
itself a breaking change. The gate enforces this against the tiers recorded in
`scripts/public_api_baseline.json`.

Promotion is a deliberate line in a diff, and it is earned: a package is
promoted when its shape has held across releases, it is documented, and it is
covered by tests.

1.0 promoted eleven packages on that evidence: `auth`, `cache`, `db`,
`events`, `guardrails`, `lifecycle`, `middleware`, `models`, `observability`,
`resilience` and `security`. Each removed no public symbol in the releases
since mid-September 2026, each has its own reference page, each is at or above
85% branch coverage, and together they are most of what the bundled plugins
import. Three of them — `cache`, `db` and `resilience` — were below the bar
until behaviour tests for their error paths and branches brought them to
94–99%; that work also fixed the defects it surfaced (a shutdown signal
received before anyone waited was lost, a one-shot `sources` iterable skipped
the feedback graph counters, and `LLMServiceProtocol.generate_response_stream`
was declared as a coroutine instead of an async generator).

Still `beta`: the packages 1.0 work is reshaping (`orchestration` and
`task_queue` around durable execution, `mcp` with its specification);
`interfaces`, whose protocols are fully covered but have no reference page of
their own; and `chat`, now at 97% branch coverage but with a known defect in
the RAG workflow handler's result shape still open. The stable tier is also
kept to at most a quarter of all packages (a test enforces it), so the next
promotion has to be worth one of the remaining slots.

## Announcing a deprecation

The policy requires a `DeprecationWarning` in the release that ships the
replacement. The decorator emits it so every announcement in the tree reads
the same and stays greppable:

```python
from baselith.core.stability import deprecated

@deprecated(since="0.37", removed_in="1.0", alternative="new_api")
def old_api(value: int) -> int:
    return new_api(value)
```

```text
DeprecationWarning: old_api is deprecated since 0.37 and will be removed in
1.0; use new_api instead.
```

It works on classes too, warning at construction, and appends a
`.. deprecated::` note to the docstring so `help()` shows it. The warning
points at the caller, not at the framework.

A whole deprecated package announces itself at import time instead, with the
same wording: a module-level `warnings.warn(_deprecation_message(...))` in its
`__init__.py`. Decorating the classes it re-exports would patch the plugin
classes themselves and warn every plugin user. That is how `core.agents` and
`core.goals` announced their removal from 0.39 until 1.0 removed them.

## What the gate reports

`scripts/check_public_api.py` states the release type a removal implies,
computed from the tier rather than recalled by the author:

```text
baselith.core.agent no longer exports Retired — BREAKING (MAJOR) — baselith.core.agent is
stable: keep the old name working for at least one MINOR, then remove it in
the change carrying the 'BREAKING CHANGE:' footer

baselith.core.swarm no longer exports Retired — MINOR — baselith.core.swarm is experimental and
promises no compatibility
```

Both still require `--update-baseline` in the same change: the baseline diff
is the review artifact. The tier decides what the change costs, not whether it
is recorded.
