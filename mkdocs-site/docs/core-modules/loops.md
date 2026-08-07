---
title: Loop Engineering
description: Verifier-owned iteration, futility detection, lesson compaction and escalation
---

**Module**: `core/loops/`

A retry loop driven by a human is not a loop — it is a person pasting error
output back into a prompt. `core/loops/` encodes the four jobs that person
was doing for free: firing the next attempt, judging whether the goal is met,
remembering what already failed, and deciding when to stop and page someone.

---

## Module Structure

```text
core/loops/
├── __init__.py
├── fingerprint.py    # failure_fingerprint / failure_lines
├── stall.py          # StallGuard, StallVerdict  (futility detection)
├── lessons.py        # LessonLog, compact_evidence (context compaction)
└── engineered.py     # EngineeredLoop, AttemptContext, LoopOutcome
```

---

## The verifier owns "done"

The model is rarely the bottleneck in a well-built loop — the verifier is. A
termination condition a shell script can check beats a smarter generator
every time, because the model never gets to declare victory on its own word.

```python
from core.loops import EngineeredLoop

async def verify() -> tuple[bool, str]:
    """Machine-checkable termination condition + evidence."""
    proc = await run_tests()
    return proc.returncode == 0, proc.output

async def act(ctx) -> None:
    await agent.run(f"{ctx.goal}\n\n{ctx.lessons}")

loop = EngineeredLoop(act=act, verify=verify, max_attempts=6, stall_threshold=3)
outcome = await loop.run("all tests pass and ruff reports zero errors")
```

If deciding "done" needs human judgment, fix the goal before building the
loop — `"improve the codebase"` never terminates.

---

## Futility detection

A watchdog notices when the agent **dies**. `StallGuard` notices when it is
alive, busy, billing, and getting nowhere: the *failure fingerprint* has not
changed for N attempts.

```python
from core.loops import StallGuard, failure_fingerprint

failure_fingerprint("FAILED test_orders.py::test_total - AssertionError")
# 'a41c92f0b3d7'  — stable across reruns, different per failure

guard = StallGuard(threshold=3)
verdict = guard.record(evidence)
if verdict.stalled:
    escalate(verdict.reason)
```

The fingerprint normalizes away everything a rerun would change (memory
addresses, timestamps, durations, temp paths, PIDs) and sorts the failure
lines, so ordering noise does not read as progress. Two attempts that fail
the same way hash identically even when the generated code differs — which
is precisely the case a failure *count* cannot see.

`threshold` must be >= 2: a single failure is never a stall.

---

## Lesson compaction

Failed attempts survive as **lessons**, not transcripts. One structured line
each — what was tried, how it failed — so attempt six is smarter than attempt
one instead of merely further from the top of the context window.

```python
from core.loops import LessonLog

log = LessonLog(max_lessons=10)
log.record(attempt=1, evidence=pytest_output, fingerprint="a41c92f0b3d7")
print(log.render())
# Previous attempts failed. Do not repeat these approaches:
# Attempt 1 failed [a41c92f0b3d7]: FAILED tests/test_orders.py::test_total - ...
```

`EngineeredLoop` does this automatically and hands the rendered block to the
actor via `AttemptContext.lessons`.

---

## Escalation

The loop must be able to lose. Every non-success ending produces a
`LoopOutcome` whose `to_state()` is a resumable hand-off for the human who
picks it up:

| Status | Meaning |
|---|---|
| `success` | The verifier confirmed the termination condition. |
| `stalled` | Same failure fingerprint N times — no longer converging. |
| `exhausted` | `max_attempts` used up. |
| `budget_exceeded` | The attached `LoopBudget` hit a cap. |

Ordinary failure never raises: losing is a documented outcome, and an
`escalate` hook that itself fails is logged without masking the result.

```python
loop = EngineeredLoop(act=act, verify=verify, escalate=page_oncall,
                      budget=LoopBudget(LoopLimits(max_iterations=8)))
```

---

## Futility inside the ReAct loop

`ReActAgent` carries two independent guards:

- `max_consecutive_tool_failures` (default `3`) — counts *how many* failures
  in a row;
- `stall_threshold` (default `None`, opt-in) — counts how many times the
  *same* failure came back, via the fingerprint above.

```python
agent = ReActAgent(tools=tools, stall_threshold=3)
```

A tool failing differently each time is still producing information; a tool
returning the identical error is burning budget. The second guard is off by
default — enabling it is an ops decision, not a silent behavior change.

---

## Related

- [Orchestration](orchestration.md) — `LoopBudget`, contracts, autonomy.
- [Reasoning](reasoning.md) — the ReAct loop the guards attach to.
- [Evaluation](evaluation.md) — the eval suite every self-modification is
  gated behind.
- [Optimization](optimization.md) — the prompt-critic loop that reads the
  failure logs a stalled loop leaves behind.
