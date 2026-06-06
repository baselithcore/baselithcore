---
title: Meta-Agent & Debate
description: Multi-persona meta-cognition and adversarial Generator-Challenger debate
---

**Module**: `core/meta/`

The Meta module implements a "Society of Mind" pattern: multiple personas generate
competing perspectives on a query, debate them across rounds, and the strongest
points are synthesized into a single balanced answer. A separate, adversarial
Generator-Challenger protocol is provided for high-stakes tasks where factual
accuracy matters more than consensus.

---

## Module Structure

```text
core/meta/
├── __init__.py                # Public exports
├── types.py                   # Perspective, DebateRound, DebateResult, MetaAgentResponse, DebateRole, ConsensusLevel
├── ensemble.py                # PersonaEnsemble
├── debate.py                  # InternalDebate
├── meta_agent.py              # MultiPersonaAgent
└── generator_challenger.py    # GeneratorChallengerProtocol (adversarial)
```

Public exports from `core.meta`:

```python
from core.meta import (
    Perspective,
    DebateRound,
    DebateResult,
    MetaAgentResponse,
    PersonaEnsemble,
    InternalDebate,
    MultiPersonaAgent,
)
```

---

## Multi-Persona Reasoning

`MultiPersonaAgent` is the high-level orchestrator. It composes a
`PersonaEnsemble` (which generates perspectives) and an `InternalDebate`
(which runs the rounds and detects consensus), then synthesizes a final answer.

### Example Usage

```python
from core.meta import MultiPersonaAgent

# Defaults: advocate + synthesizer + devil's-advocate ensemble, 3 debate rounds
meta = MultiPersonaAgent()

response = await meta.process(
    query="What is the best caching architecture for a distributed multi-tenant system?",
    context={"scale": "10k tenants"},  # optional
)

print(response.final_answer)
print(response.synthesis_rationale)
print(f"Confidence: {response.confidence}")
print(f"Perspectives considered: {response.perspective_count}")

# Inspect the debate
result = response.debate_result
print(f"Rounds: {result.total_rounds}")
print(f"Consensus: {result.consensus_level.value}")
print(f"Key agreements: {result.key_points}")
print(f"Unresolved tensions: {result.unresolved_tensions}")
print(f"Winning perspective: {result.winning_perspective}")
```

`process()` is async. A synchronous wrapper `process_sync(query, context=None)`
exists but raises `RuntimeError` if called from within a running event loop.

You can extend the ensemble with custom personas:

```python
from core.personas import Persona
from core.meta.types import DebateRole

meta.add_persona(
    Persona(name="security_reviewer", description="Hunts for attack surface"),
    role=DebateRole.CRITIC,
)
print(meta.persona_names)
```

### The Ensemble

`PersonaEnsemble(personas=None, include_devil_advocate=True)` generates one
`Perspective` per persona via `generate_perspectives(query, context=None)`.
With no custom personas it uses the built-in `advocate` and `synthesizer`
personas, plus a `devils_advocate` critic when `include_devil_advocate=True`.
Roles (`DebateRole.ADVOCATE` / `CRITIC` / `SYNTHESIZER`) are auto-assigned from
each persona's name.

### The Debate

`InternalDebate(max_rounds=3, consensus_threshold=0.7)` drives the rounds via
`run(perspectives, query)`, returning a `DebateResult`. Each round generates
arguments, critic-to-advocate counterarguments, and extracts agreements /
disagreements. The loop stops early on consensus or stagnation.

Internals:

- `_find_agreements_disagreements()` uses LLM semantic analysis to extract
  structured agreements and disagreements, falling back to a keyword-overlap
  heuristic (`_analyze_agreement_heuristic`) when no LLM service is available.
- `_determine_winner()` scores each non-critic perspective by base confidence
  plus a bonus for being named in agreements, and returns the highest scorer as
  `DebateResult.winning_perspective`.

### Data Types (`types.py`)

| Symbol | Kind | Notes |
|--------|------|-------|
| `DebateRole` | Enum | `ADVOCATE` / `CRITIC` / `MEDIATOR` / `SYNTHESIZER` |
| `ConsensusLevel` | Enum | `FULL` / `MAJORITY` / `PARTIAL` / `NONE` |
| `Perspective` | dataclass | `persona_name`, `role`, `content`, `confidence`, `reasoning`, `metadata`; `.is_critical` |
| `DebateRound` | dataclass | `round_number`, `arguments`, `counterarguments`, `agreements`, `disagreements`; `.has_movement` |
| `DebateResult` | dataclass | `rounds`, `consensus_level`, `key_points`, `unresolved_tensions`, `winning_perspective`; `.total_rounds`, `.reached_consensus` |
| `MetaAgentResponse` | dataclass | `final_answer`, `perspectives`, `debate_result`, `synthesis_rationale`, `confidence`, `metadata`; `.perspective_count` |

!!! note
    `core.meta.types` also defines a `DebateRound` dataclass. The
    Generator-Challenger protocol (below) defines its **own** `DebateRound`
    dataclass in `generator_challenger.py` with a different shape. Import from
    the module you mean to avoid confusion.

---

## Generator-Challenger Protocol

The multi-persona `InternalDebate` is consensus-oriented. For high-stakes tasks
where factual accuracy matters more than consensus, use
`GeneratorChallengerProtocol` in
[`core/meta/generator_challenger.py`](https://github.com/baselithcore/baselithcore/blob/main/core/meta/generator_challenger.py).

| Symbol | Purpose |
|--------|---------|
| `GeneratorChallengerProtocol` | Bounded adversarial loop driver |
| `Critique` | Output of a Challenger turn (`text`, optional `verdict_hint`) |
| `Verdict` | `APPROVED` / `REVISE` / `REJECT` |
| `DebateRound` | Immutable record of one round (`round_index`, `generator_output`, `critique`, `verdict`) |
| `DebateOutcome` | `final_answer`, `final_verdict`, `rounds`, `history`; `.approved`, `.round_count` |

The protocol is LLM-agnostic: pass three callables — Generator, Challenger,
Judge — each of which may be sync or async. The protocol drives them up to
`max_rounds`. The loop terminates on the first terminal verdict (by default
`APPROVED` or `REJECT`).

### Example Usage

```python
from core.meta.generator_challenger import (
    GeneratorChallengerProtocol,
    Critique,
    Verdict,
)

async def gen(prompt, rounds):
    return await llm.complete(prompt)

async def challenger(answer, rounds):
    text = await llm.critique(answer)
    return Critique(text=text)

async def judge(answer, critique):
    return await llm.judge(answer, critique.text)  # returns a Verdict

proto = GeneratorChallengerProtocol(max_rounds=3)
outcome = await proto.run(
    "Draft the migration plan",
    generator=gen,
    challenger=challenger,
    judge=judge,
)

if outcome.approved:
    publish(outcome.final_answer)
else:
    escalate_to_human(outcome.history)
```

The constructor is keyword-only and accepts `max_rounds` (must be `> 0`),
`accept_verdicts` (defaults to `(Verdict.APPROVED,)`), and `terminal_verdicts`
(defaults to `(Verdict.APPROVED, Verdict.REJECT)`; the accept set is always
merged in). `run()` always returns a `DebateOutcome`, and raises `TypeError` if
a callable returns the wrong type (generator must return `str`, challenger a
`Critique`, judge a `Verdict`).

!!! note "When to pick which"
    `MultiPersonaAgent` / `InternalDebate` excel at brainstorming and synthesis
    across multiple expert lenses. `GeneratorChallengerProtocol` excels when one
    answer must be hardened against a hostile reviewer — use it for code review,
    security analysis, and refusal-policy validation.
