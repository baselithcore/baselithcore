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
from core.personas import Persona, PersonaManager

manager = PersonaManager()

# Define the persona traits
expert = Persona(
    name="TechExpert",
    traits=["technical", "precise", "detailed"],
    tone="formal",
    expertise=["software architecture", "AI systems", "cloud deployments"]
)

# Apply the persona to an agent instance
agent.set_persona(expert)
```

## Persona Management

The `PersonaManager` allows for dynamic loading and switching of identities during execution:

- **Registry**: Load Personas from configuration files or databases.
- **Dynamic Swapping**: A Meta-Agent can instruct sub-agents to change their Persona based on the current user's profile (e.g., switching from `TechExpert` to `FriendlySupport` when talking to non-technical users).
