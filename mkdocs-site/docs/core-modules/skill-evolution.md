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
  side effects ("model proposes, code disposes").
- **`ManagedSkillWriter`** — versioned writes under the managed root
  (default `data/skills/managed/`): active `SKILL.md`,
  `.versions/<n>.md` history, `meta.json` (`version`, `best_score`),
  `rollback()`.
- **`SkillGate`** — `review(name, validate)` accepts a version only when
  its validation score strictly beats the recorded best; otherwise the
  skill is rolled back. **The pattern store is never touched on
  rejection** — knowledge persists even when the skill built from it
  regresses.
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
