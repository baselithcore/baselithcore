# Skill Evolution

`core/skill_evolution/` closes the experience→knowledge→skill loop:
run outcomes are distilled into a **persistent, deduplicated pattern
store** (the *wiki* layer), recurring patterns are **compiled into
declarative skills** ([Declarative Skills](skills.md)), and every new
skill version passes a **validation gate** that can roll the skill back —
while the accumulated knowledge persists regardless.

## Layers

| Layer | Implementation |
| --- | --- |
| Raw execution traces | Existing seams: checkpoints, run events, evaluation events |
| Wiki (persistent knowledge) | `PatternStore` — in-memory or Postgres (`agent_patterns`, migration `006`) |
| Executable skills | The declarative `SKILL.md` catalog + a managed skills root |

Patterns are keyed by failure fingerprint
([Loop Engineering](loops.md)): re-observing a known failure merges into
the existing pattern (occurrence count + evidence) instead of piling up
free-form strings.

## Components

- **`WikiMaintainer`** — distills `EVALUATION_COMPLETED` events (low
  score ⇒ `failure_mode` pattern, high score ⇒ `strategy` pattern) and
  persists engineered-loop `LessonLog` lessons via `persist_lessons()`.
  An optional injected async `rca` callable (LLM root-cause analysis)
  refines failure summaries; errors fall back to the deterministic
  compaction.
- **`SkillProposer`** — selects recurring `candidate` patterns
  (`min_occurrences`) and prompts an injected async `generate` callable
  for a complete `SKILL.md`. Malformed generations are dropped without
  side effects ("model proposes, code disposes"). Source patterns of a
  rejected proposal go on a **rejection cooldown** (`record_rejection`,
  `rejection_cooldown_seconds` default `86_400.0` — one day) and are
  filtered from candidate selection until it expires, so the same broken
  synthesis is not re-proposed on every cycle.
- **`ManagedSkillWriter`** — versioned writes under the managed root
  (default `data/skills/managed/`): active `SKILL.md`,
  `.versions/<n>.md` history, `meta.json` (`version`, `best_score`),
  `rollback()`.
- **`SkillGate`** — `review(name, validate)` accepts a version only when
  its validation score strictly beats the recorded best; otherwise the
  skill is rolled back. The validator may return a scalar score **or a
  `FitnessVector`** (see
  [Governed self-modification](#governed-self-modification)). **The
  pattern store is never touched on rejection** — knowledge persists even
  when the skill built from it regresses. Decisions are audited as
  `self_modify.apply` / `self_modify.reject`.
- **`SkillImpactTracker`** — correlates skill activations with run
  outcomes (per-`run_id` buckets, LRU-capped; windowed fallback), wired
  through `SkillService(on_activate=...)`.
- **`SkillEvolutionService`** — facade: `start()` subscribes to
  evaluation events; `evolve(validate)` runs one explicit propose→gate
  cycle; `build_skill_evolution_service()` picks the Postgres store when
  Postgres is enabled.

## Wiring

```python
from core.plugins.skills_service import SkillService
from core.skill_evolution import build_skill_evolution_service, make_activation_guard

evolution = build_skill_evolution_service(generate=my_llm_callable)
evolution.start()  # evaluations now feed the wiki + impact tracker

# Preferred seam: evolved skills flow through the registry's normal
# discovery path (also works for an orchestrator-built SkillService).
registry.register_skill_root("managed", evolution.writer.root)

skill_service = SkillService(
    registry,
    on_activate=evolution.impact.record_activation,
    activation_guard=make_activation_guard(evolution.writer),
)

decision = await evolution.evolve(validate=my_regression_validator)
```

Evolved skills enter the normal catalog and are served with the same
progressive disclosure as plugin-shipped skills — the wiki itself is
never injected into prompts.

## Distillation into retrieval

The pattern store accumulates *what worked*, but that knowledge only pays
off when it reaches a prompt. `core/skill_evolution/distillation.py`
(exported from `core.skill_evolution`) closes that loop on the few-shot
side: `STRATEGY` patterns become
[`FewShotExample`](personas.md) entries the persona manager splices into
system prompts like any curated example.

```python
from core.personas.few_shot import FewShotLibrary
from core.skill_evolution import sync_strategies_to_library

library = FewShotLibrary()
added = await sync_strategies_to_library(
    store, library, task="research", min_occurrences=3
)
```

- **Eligibility** — `STRATEGY` patterns only, either `PROMOTED` or a
  `CANDIDATE` observed at least `min_occurrences` times (default `3`);
  `RETIRED` and empty-titled/empty-summary patterns are skipped. Each
  example maps the pattern title to its distilled summary
  (`input` → `output`) with provenance in the rationale.
- **Idempotent** — each example carries a `pattern:<fingerprint>` tag; a
  fingerprint already present in the task bucket is not added again, so
  the sync is safe to call on a schedule (returns the number actually
  added, `0` when everything eligible is registered). The dedup key lives
  in the example's own tags, so the guarantee holds across separate calls
  sharing one library.
- `patterns_to_few_shot(patterns, library, task=, min_occurrences=)` is
  the pure in-memory form when you already hold the pattern list.

The second retrieval seam is loop **priming**: `prime_lessons`
(`core.loops.priming`) ranks the pattern store against a campaign goal
with BM25 and renders a bounded "Lessons from past campaigns" block —
see [Loop Engineering › Priming](loops.md#priming-the-first-attempt-primingpy).

## Governed self-modification

A synthesized skill changes the system's **own future behavior**, so the
propose→gate cycle is governed like any high-stakes action:

- **Audit trail.** `evolve()` records `self_modify.propose` when a
  proposal enters the gate; `SkillGate.review` records
  `self_modify.apply` / `self_modify.reject` with the score, previous
  best, version and (for vector validators) the fitness breakdown. See
  [Audit Trail](audit-trail.md#self-modification-self_modify).
- **Human approval.** Pass `autonomy_policy=` (and optionally
  `human_intervention=`) to `evolve()`: an eval-accepted skill
  additionally requires human approval under the `self_modify` autonomy
  category — required at `SUPERVISED` **and** `SEMI_AUTONOMOUS`,
  automatic only at `FULLY_AUTONOMOUS` (see
  [Orchestration › AutonomyPolicy](orchestration.md#autonomypolicy-three-tier-spectrum)).
  Denied or unavailable approval rolls the version back (fail closed).
  `None` keeps the eval-gate-only behavior.
- **Rejection cooldown.** The source patterns of a rejected proposal go on
  the proposer's cooldown: they stay `CANDIDATE` (re-proposable), but not
  on the very next cycle.

```python
from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy

decision = await evolution.evolve(
    validate=my_regression_validator,
    autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SEMI_AUTONOMOUS),
    human_intervention=intervention,      # approval channel
)
```

### Multi-objective fitness (`FitnessVector`)

A single accuracy-ish scalar lets evolution trade unbounded latency and
cost for marginal quality. A validator may instead return a
`FitnessVector` (`core/skill_evolution/types.py`), making the trade
explicit:

```python
from core.skill_evolution.types import FitnessVector

async def validate(skill_name: str) -> FitnessVector:
    report = await replay_suite(skill_name)
    return FitnessVector(
        quality=report.pass_rate,          # required, in [0, 1]
        latency_s=report.avg_latency,
        cost_usd=report.total_cost,
    )
```

The gate compares `scalarize()` — `quality − latency_weight·latency_s −
cost_weight·cost_usd`, clamped at 0 (default weights: `0.005` per second
of validation latency, `0.1` per USD of validation cost, both overridable
on the vector) — so a version that is barely better but far slower or
pricier loses. Scalar-returning validators are unchanged.

## Safety posture

- **Single distillation owner**: only `SkillEvolutionService` subscribes
  to `EVALUATION_COMPLETED` — a second bridge would double-count pattern
  occurrences.
- **Fail-closed gate**: a raising validator rejects (and rolls back) even
  the first version; `evolve()` refuses to run without proposer + gate +
  validator. Source patterns are promoted only after acceptance, so
  rejected knowledge stays re-proposable.
- **Integrity**: the writer records a SHA-256 of every accepted version in
  `meta.json`; `make_activation_guard` blocks activation of a managed
  skill whose on-disk content no longer matches (the same fail-closed
  model as plugin `integrity_sha256`).
- **Tenancy**: patterns are tenant-scoped (Postgres), but managed skills
  are a shared catalog — `evolve()` therefore refuses synthesis under a
  non-default tenant unless `allow_tenant_synthesis=True`.
- **Unscored events** (missing/None score) are skipped, never fabricated
  into failures; integer run ids (including `0`) attribute correctly.

## Notes

- The propose→gate cycle is explicit, not scheduled: callers choose the
  cadence and supply the LLM (`generate`) and validator callables.
- `EVALUATION_COMPLETED` carries `run_id` and `response` end-to-end from
  the orchestrator's `FLOW_COMPLETED`.
- Impact attribution without `run_id` uses a process-wide window —
  accurate only for single-loop processes.
- A managed skill whose generated name collides with a plugin-shipped
  skill is shadowed by the plugin's (first-provider-wins); the collision
  is logged at catalog refresh.
