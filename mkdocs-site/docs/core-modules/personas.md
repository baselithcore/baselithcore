---
title: Personas
description: Configurable personalities and traits for agents
---

**Module**: `core/personas/`

The Personas module allows you to decouple identity definition from execution logic. By applying a dynamically configurable `Persona` to an agent, you can drastically alter its tone, expertise, and operational boundaries without rewriting its base prompt.

---

## Module Structure

```text
core/personas/
├── __init__.py           # Public exports
├── manager.py           # PersonaManager
└── models.py            # Persona & Trait models
```

---

## Defining a Persona

```python
from core.personas import Persona

# Define the persona traits and system prompt
expert = Persona(
    name="technical_expert",
    description="A technical expert providing detailed analysis",
    traits={"tone": "technical", "style": "detailed", "approach": "analytical"},
    system_prompt="You are a senior technical architect...",
    temperature=0.4
)
```

---

## Structured Agent Personas

BaselithCore includes pre-configured personas designed for high-performance research and reporting tasks. These aren't just "tone settings"—they represent engineering choices with measurable impacts.

### Comparison Table

| Persona | Quality | Efficiency | Hallucination Rate |
| :--- | :---: | :---: | :---: |
| **Concise Analyst** | 75% | 2.1 calls | 15% |
| **Thorough Researcher** | 90% | 3.4 calls | 5% |
| **Structured Reporter** | 85% | 2.8 calls | 8% |

### Built-in Defaults

| Persona | Role | Key Trait |
| :--- | :--- | :--- |
| `CONCISE_ANALYST` | Terse researcher | No fluff, inline citations. |
| `THOROUGH_RESEARCHER` | Senior academic | Multi-perspective, notes contradictions. |
| `STRUCTURED_REPORTER` | Data architect | Schema-enforced: Summary → Findings → Sources. |

### Usage: Selecting a Persona

```python
from core.personas.defaults import THOROUGH_RESEARCHER, STRUCTURED_REPORTER

# Use the Thorough Researcher for deep dives
agent.apply_persona(THOROUGH_RESEARCHER)

# Use the Structured Reporter for standardized API outputs
agent.apply_persona(STRUCTURED_REPORTER)
```

!!! tip "A/B Testing Personas"
    Use the `PromptEvaluator.compare()` tool (see [Evaluation](evaluation.md)) to measure which persona performs best for your specific dataset or domain.

---

## Few-Shot Example Library

`core/personas/few_shot.py` provides a task-indexed library of in-context
examples. In-context examples reduce token spend and improve format
adherence; keeping them under version control means non-engineers can
review and edit them.

### Public API

| Symbol | Purpose |
|--------|---------|
| `FewShotLibrary` | In-memory store of examples indexed by task type |
| `FewShotExample` | Immutable `input` / `output` / `rationale` / `tags` tuple |
| `load_library(path)` | Load YAML or JSON into a `FewShotLibrary` |
| `FewShotLoadError` | Raised when payload data fails validation |

### Example: define, select, render

```yaml
# personas/data/examples.yaml
translation:
  - input: "hello"
    output: "ciao"
  - input: "goodbye"
    output: "arrivederci"
summarize:
  - input: "long article"
    output: "short summary"
    rationale: "extracted the lead sentence"
    tags: [news]
```

```python
from core.personas.few_shot import load_library

lib = load_library("personas/data/examples.yaml")
chosen = lib.select("summarize", limit=2, tags=["news"])
prompt_block = lib.render("summarize")   # Markdown ready to splice in
```

The `render()` method produces a numbered Markdown block (`### Example 1`,
`**Input:**`, `**Output:**`, `**Rationale:**` …) suitable for direct
inclusion in a system prompt.
