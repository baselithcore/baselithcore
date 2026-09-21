---
title: Python API
description: The `baselith` package — the public surface for programs that build on the framework
---

Programs that build on BaselithCore import **`baselith`**:

```python
from baselith import Agent, Crew, Task
```

That is the whole public Python surface. The implementation lives under
`core.*` — a package name that only makes sense from inside the framework's
own repository — and those imports keep working unchanged. `baselith` is the
name that carries a compatibility promise.

## What it exports

| Group | Names |
| --- | --- |
| Typed agents | `Agent`, `AgentResult`, `AgentOutputValidationError` |
| Crews | `Crew`, `Task`, `TaskResult`, `CrewResult`, `AgentUsage`, `CostFn`, `ReviewDecision`, `ReviewVerdict` |
| Group chat | `GroupChat`, `GroupChatResult`, `Participant`, `ChatMessage`, `RoundRobinSelector`, `LLMManagerSelector`, `CapabilitySelector`, `SpeakerSelector` |
| Tools & skills | `ToolDefinition`, `SkillResult`, `ok`, `fail`, `partial` |
| Runtime limits | `LoopBudget`, `AgentContract`, `AutonomyPolicy` |
| Server & config | `create_app`, `get_app_config`, `get_core_config` |
| Version | `__version__` |

`dir(baselith)` lists them at runtime, and
`python scripts/check_public_api.py --list` prints the frozen snapshot the
gate compares against.

## Importing is cheap

Attribute access is lazy ([PEP 562][pep562]). `import baselith` resolves
nothing: the module for a name is imported the first time that name is read,
then cached in the module globals.

| Import | Time | Modules loaded |
| --- | --- | --- |
| `import baselith` | ~2 ms | 79 |
| `from baselith import SkillResult` | ~0.05 s | 227 |
| `from baselith import Agent` | ~0.28 s | 1 108 |

The difference is not cosmetic. Reaching `Agent` wires the orchestrator and
the LLM service; a program that only needs `SkillResult` to type a tool's
return value should not pay for them, and a CLI that imports the package to
read `__version__` should pay for nothing at all.

The packages behind the facade are lazy for the same reason — see
[import-time laziness](../advanced/lazy-loading.md#import-time-laziness).
`tests/unit/test_import_cost.py` holds these paths to a module budget so the
numbers above cannot quietly rot.

!!! note "The numbers are indicative"
    Measured on one machine with a warm filesystem cache. What matters is the
    ratio between the rows, not the absolute figures.

## What the promise covers

Everything reachable from `baselith` is **`stable`**: it does not move or
change shape without a deprecation cycle and a MAJOR release. A symbol
reachable only from a `core.*` package carries the tier that package declares
— see [API Stability Tiers](../advanced/api-stability.md).

`LoopBudget` illustrates the distinction. As `baselith.LoopBudget` it is
stable. The package it happens to live in, `core.orchestration`, is `beta`
and its other exports may still move.

## Example

```python
from pydantic import BaseModel

from baselith import Agent, SkillResult, ok

class CityInfo(BaseModel):
    city: str
    population: int

async def lookup_population(city: str) -> SkillResult:
    """Look up a city's population."""
    return ok(data={"city": city, "population": 2_873_000})

agent = Agent(output_type=CityInfo, tools=[lookup_population])
result = await agent.run("Tell me about Rome")
result.output  # -> CityInfo, validated
```

[pep562]: https://peps.python.org/pep-0562/
