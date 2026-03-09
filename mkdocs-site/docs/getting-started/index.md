---
title: Getting Started
description: Start developing with BaselithCore
---

Welcome to the **BaselithCore** getting started guide. This section will guide you through installation, configuration, and creating your first plugin.

---

## Recommended Path

<div class="feature-grid" markdown>

<div class="feature-card" markdown>

### :material-download: 1. Installation

Set up your development environment and install dependencies.

[:octicons-arrow-right-24: Installation Guide](installation.md)

</div>

<div class="feature-card" markdown>

### :material-rocket-launch: 2. Quick Start

Launch the system and verify everything works.

[:octicons-arrow-right-24: Quick Start](quickstart.md)

</div>

<div class="feature-card" markdown>

### :material-puzzle-plus: 3. First Plugin

Create your first plugin following the step-by-step tutorial.

[:octicons-arrow-right-24: Tutorial](first-plugin.md)

</div>

</div>

---

## Prerequisites

Before you begin, ensure you have:

| Requirement | Minimum Version | Notes |
|-------------|-----------------|-------|
| Python | 3.10+ | Python 3.11 recommended |
| Redis | 7.0+ | For caching and task queues |
| PostgreSQL | 14+ | For structured persistence |
| Node.js | 18+ | Only for frontend development |

### Developer Skills

This framework is designed for developers with:

- **Python proficiency**: Async/await, type hints, protocols
- **API development**: REST, WebSocket, asynchronous patterns
- **Containerization**: Docker and Docker Compose basics
- **AI/LLM familiarity**: Understanding of prompt engineering and LLM integration

---

## Architecture Overview

The framework is structured in distinct layers:

```text
┌─────────────────────────────────────────────┐
│              Plugin Layer                    │
│  (Domain-specific logic, agents, handlers)  │
├─────────────────────────────────────────────┤
│              Core Framework                  │
│  (Orchestration, Services, Memory, DI)      │
├─────────────────────────────────────────────┤
│              Infrastructure                  │
│  (Redis, PostgreSQL, Qdrant, LLM Providers) │
└─────────────────────────────────────────────┘
```

!!! tip "Fundamental Principle"
    The **Core** is domain-agnostic. Any domain-specific logic (e.g., Jira integration, document analysis) must be implemented as a **Plugin**.

---

## Next Steps

1. **[Installation](installation.md)** - Set up the environment
2. **[Quick Start](quickstart.md)** - Launch the system  
3. **[First Plugin](first-plugin.md)** - Create a plugin
4. **[Architecture](../architecture/overview.md)** - Deep dive into the design
