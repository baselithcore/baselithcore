<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="media/full-white-og.png">
    <source media="(prefers-color-scheme: light)" srcset="media/full-black-og.png">
    <img alt="BaselithCore Logo" src="media/full-black-og.png" width="500">
  </picture>
</p>

# BaselithCore

> **The Research-Backed Engine for Production-Grade Agentic AI.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg?style=for-the-badge)](LICENSE)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg?style=for-the-badge)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg?style=for-the-badge)](http://mypy-lang.org/)
[![Tests: 5321/5324 | 77%](https://img.shields.io/badge/Tests-5321%2F5324%7C77%25-brightgreen.svg?style=for-the-badge)](tests/)
[![PyPI version](https://img.shields.io/pypi/v/baselith-core.svg?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/p/baselith-core/)

[![EU AI Act toolkit](https://img.shields.io/badge/EU_AI_Act-Compliance_Toolkit-0b5394.svg?style=for-the-badge)](https://docs.baselithcore.xyz/advanced/regulatory-compliance/)
[![World Model: MCTS](https://img.shields.io/badge/World_Model-MCTS-teal.svg?style=for-the-badge)](https://docs.baselithcore.xyz/core-modules/world-model/)
[![Swarm Intelligence](https://img.shields.io/badge/Swarm-Intelligence-indigo.svg?style=for-the-badge)](https://docs.baselithcore.xyz/core-modules/swarm/)
[![Native MCP](https://img.shields.io/badge/Native-MCP-blue.svg?style=for-the-badge)](https://docs.baselithcore.xyz/core-modules/mcp/)
[![Docker Ready](https://img.shields.io/badge/docker-ready-blue.svg?style=for-the-badge&logo=docker&logoColor=white)](https://github.com/baselithcore/baselithcore/blob/main/Dockerfile-full)

---

**BaselithCore** is a high-performance orchestration engine designed to transition agentic AI from experimental prototypes to resilient, production-ready infrastructure. Built on a modular architecture, it provides an agnostic foundation for engineering scalable multi-agent systems.

<div align="center">

[**Quick Start**](#quick-start) | [**Documentation**](https://docs.baselithcore.xyz) | [**Architecture**](https://docs.baselithcore.xyz/architecture/) | [**Plugin System**](https://docs.baselithcore.xyz/plugins/)

</div>

---

## Why BaselithCore

- **Agents that survive production.** Durable execution with checkpoint/resume, replayable tool steps, state history and fork/rewind — a crash mid-run recovers without duplicating a single side effect.
- **Everything included, nothing rented.** Evaluation suites, LLM-as-judge, red-teaming, OpenTelemetry tracing, Prometheus metrics with real USD cost, Helm/Terraform deployment — built in and self-hosted, with no companion SaaS to subscribe to.
- **Brakes, not just horsepower.** Autonomy gating, durable human-in-the-loop approvals, per-request cost budgets, prompt-injection guardrails, sandboxed code execution — every seam fail-closed by default, plus opt-in EU AI Act / GDPR compliance primitives.

Ten lines to a working multi-agent pipeline — typed, budgeted, observable:

```python
from core.agent import Agent, Crew, Task

researcher = Agent(system_prompt="You are a meticulous researcher.")
writer = Agent(system_prompt="You write crisp executive summaries.")

crew = Crew(agents=[researcher, writer], tasks=[
    Task("Research {topic} and list the key facts.", agent=researcher),
    Task("Write a summary from the research.", agent=writer),
])
report = (await crew.run(inputs={"topic": "vector databases"})).final
```

## Architecture at a Glance

```mermaid
graph TD
    subgraph SC["Sacred Core (Agnostic Engine)"]
        A["Core Orchestrator<br/>(intent · routing · adaptive loop · durable checkpoint/resume)"]
        F["Flow Handlers"]

        subgraph COG["Cognitive Layer"]
            RE["Reasoning<br/>(MCTS · Tree-of-Thoughts)"]
            WM["World Model<br/>(risk · rollback · simulation)"]
            SW["Swarm<br/>(auction protocols)"]
            PL["Planning"]
            MT["Meta · Reflection · Adversarial"]
        end

        M["Memory Hierarchy<br/>(STM → MTM → LTM)"]
        S["Storage Layer<br/>(Postgres · Qdrant/pgvector · Redis)"]
        R["Plugin Registry"]
        RES["Resilience · Observability · Guardrails"]
    end

    A --> COG
    A --> F
    A --> M
    M --> S
    COG --> M

    R --> C["Custom Agent Plugins"]
    R --> D["Capability Extensions"]
    R -.->|Inject Handlers| A
    R -.->|Inject Routers| G["API Gateway"]

    A --> H["LLM Layer<br/>(Anthropic · OpenAI · Gemini · Ollama · HF)<br/>native tool-calling · typed output · cross-provider fallback"]
    F --> H

    A --> I["Interop<br/>(MCP · A2A streaming)"]
    A -.->|wrapped by| RES
```

The full deep-dive lives in the [architecture docs](https://docs.baselithcore.xyz/architecture/overview/).

## Core Philosophy

1. **Sacred Core**: The `core/` directory contains exclusively agnostic logic — orchestration, infrastructure, utilities. No domain code, ever.
2. **Plugin-First**: All business logic, integrations, and specialized capabilities live in **Plugins**, so secondary features never bloat the engine.
3. **Agentic by Design**: The Agentic Design Patterns (Memory, Reflection, Tool Use, Planning, …) are baked into the orchestrator, not bolted on.

## What's Inside

Every capability ships production-hardened: typed, tested, observable, and fail-closed by default.

| | |
| :-- | :-- |
| **Typed agents & declarative crews** | Single-import `Agent`, multi-agent `Crew` in ten lines → [Agent API](https://docs.baselithcore.xyz/core-modules/agent/) |
| **Durable execution & time-travel** | Checkpoint/resume, replayable tool steps, state history, fork/rewind → [Orchestration](https://docs.baselithcore.xyz/core-modules/orchestration/) |
| **Structured event streaming** | Per-run agent events in-process or over SSE → [Orchestration](https://docs.baselithcore.xyz/core-modules/orchestration/) |
| **Cognitive layer** | MCTS, Tree-of-Thoughts, world model, swarm auctions & handoffs → [Reasoning](https://docs.baselithcore.xyz/core-modules/reasoning/) · [Swarm](https://docs.baselithcore.xyz/core-modules/swarm/) |
| **Governance & safety** | Autonomy gating, durable human-in-the-loop, loop budgets, guardrails, sandboxed code → [Autonomy & Safety](https://docs.baselithcore.xyz/core-modules/orchestration/) |
| **Memory & RAG** | STM→MTM→LTM hierarchy, hybrid search, full RAG pipeline, Qdrant or pgvector backends → [Memory](https://docs.baselithcore.xyz/core-modules/memory/) · [Services](https://docs.baselithcore.xyz/core-modules/services/) |
| **Interoperability** | Native dual-era MCP (server + client), A2A peer interop → [MCP](https://docs.baselithcore.xyz/core-modules/mcp/) · [A2A](https://docs.baselithcore.xyz/core-modules/a2a/) |
| **Evaluation & observability** | Trajectory eval in CI, LLM-as-judge, red-team, OTel + Prometheus with USD cost metrics → [Evaluation](https://docs.baselithcore.xyz/core-modules/evaluation/) |
| **Regulatory toolkit** | Opt-in EU AI Act / GDPR / NIS2 / DORA primitives with evidence trails → [Regulatory Compliance](https://docs.baselithcore.xyz/advanced/regulatory-compliance/) |
| **Production deployment** | Docker, Helm, Terraform, SLO rules, typed SDKs → [Deployment](https://docs.baselithcore.xyz/advanced/deployment/) |

## <span id="quick-start"></span> Quick Start

```bash
pip install baselith-core       # core engine
docker compose up -d            # Redis, PostgreSQL, Qdrant (optional)
baselith doctor                 # validate environment and configuration
```

Optional capabilities (RAG, browser automation, OCR, extra model providers, vector backends, …) install as extras — see the [installation guide](https://docs.baselithcore.xyz/getting-started/installation/) for the full list, and the [quickstart](https://docs.baselithcore.xyz/getting-started/quickstart/) to build your first agent.

## Resources

| Resource                                                                             | Description                                           |
| :----------------------------------------------------------------------------------- | :---------------------------------------------------- |
| [**Official Website**](https://baselithcore.xyz)                                     | The core landing page for the BaselithCore framework. |
| [**Official Documentation**](https://docs.baselithcore.xyz)                          | The official docs for the BaselithCore framework.     |
| [**Architecture**](https://docs.baselithcore.xyz/architecture/overview/)             | Deep dive into the "Sacred Core" and design choices.  |
| [**Plugin Guide**](https://docs.baselithcore.xyz/plugins/architecture/)              | How to extend BaselithCore using the plugin system.   |
| [**Agentic Patterns**](https://docs.baselithcore.xyz/architecture/agentic-patterns/) | Implementation of Agentic Design Patterns.            |
| [**Regulatory Compliance**](https://docs.baselithcore.xyz/advanced/regulatory-compliance/) | AI Act, GDPR, NIS2 and DORA mapped article by article — gaps included. |
| [**Deployment**](https://docs.baselithcore.xyz/advanced/deployment/)                 | Production-ready deployment strategies.               |

## Contributing & License

We welcome contributions that adhere to our code standards. Please review [CONTRIBUTING.md](CONTRIBUTING.md).

BaselithCore is licensed under the **GNU Affero General Public License v3.0 (AGPL v3)**.
See [LICENSE](LICENSE) for full details.

---
Copyright © 2026 BaselithCore Team.
