<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="media/full-white-og.png">
    <source media="(prefers-color-scheme: light)" srcset="media/full-black-og.png">
    <img alt="BaselithCore Logo" src="media/full-black-og.png" width="500">
  </picture>
</p>

# BaselithCore

> **The Research-Backed Engine for Production-Grade Agentic AI.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg?style=for-the-badge)](LICENSE)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg?style=for-the-badge)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg?style=for-the-badge)](http://mypy-lang.org/)
[![Tests: 1693/1693 | 69%](https://img.shields.io/badge/Tests-1693%2F1693_--_69%25-brightgreen.svg?style=for-the-badge)](tests/)
[![PyPI version](https://img.shields.io/pypi/v/baselith-core.svg?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/baselith-core/)

[![World Model: MCTS](https://img.shields.io/badge/World_Model-MCTS-teal.svg?style=for-the-badge)](mkdocs-site/docs/core-modules/world-model.md)
[![Swarm Intelligence](https://img.shields.io/badge/Swarm-Intelligence-indigo.svg?style=for-the-badge)](mkdocs-site/docs/core-modules/swarm.md)
[![Agentic Patterns](https://img.shields.io/badge/Patterns-20+_Agentic-orange.svg?style=for-the-badge)](mkdocs-site/docs/architecture/agentic-patterns.md)
[![Native MCP](https://img.shields.io/badge/Native-MCP-blue.svg?style=for-the-badge)](mkdocs-site/docs/core-modules/mcp.md)
[![Docker Ready](https://img.shields.io/badge/docker-ready-blue.svg?style=for-the-badge&logo=docker&logoColor=white)](https://github.com/baselithcore/baselithcore/blob/main/Dockerfile-full)

---

**BaselithCore** is a high-performance orchestration engine designed to transition agentic AI from experimental prototypes to resilient, production-ready infrastructure. Built on a modular architecture, it provides an agnostic foundation for engineering scalable multi-agent systems.

<div align="center">

[**Quick Start**](#quick-start) | [**Architecture**](https://docs.baselithcore.xyz/architecture/) | [**Plugin System**](https://docs.baselithcore.xyz/plugins/architecture/) | [**API Reference**](https://docs.baselithcore.xyz/api/)

</div>

---

## Core Philosophy

BaselithCore is governed by a strict architectural separation:

1. **Sacred Core**: The `core/` directory contains exclusively agnostic logic—orchestration, infrastructure, and utilities. It remains untainted by domain-specific logic.
2. **Plugin-First**: All business logic, external integrations, and specialized capabilities are implemented as **Plugins**, ensuring secondary features never bloat the primary engine.
3. **Agentic by Design**: Native adherence to the Agentic Design Patterns (Memory, Reflection, Tool Use, etc.) is baked into the orchestrator.

### Architecture Overview

```mermaid
graph TD
    subgraph "Sacred Core (Agnostic Engine)"
        A["Core Orchestrator"]
        M["Memory Hierarchy (STM/MTM/LTM)"]
        S["Storage Layer (DB/Vector)"]
        R["Plugin Registry"]
    end

    R --> C["Custom Agent Plugins"]
    R --> D["Capability Extensions"]
    
    A --> M
    M --> S
    A --> F["Flow Handlers"]
    
    R -.->|Inject Handlers| A
    R -.->|Inject Routers| G["API Gateway"]
    
    A --> H["LLM Layer (Anthropic, OpenAI, Ollama, HF)"]
    F --> H
```

---

## Key Capabilities

### Cognitive Orchestration

We manage the complexity of agentic reasoning so you can focus on domain value.

* **Strategic Optimization**: Native **Monte Carlo Tree Search (MCTS)** and **Tree of Thoughts** for advanced decision-making and "What-If" simulations.
* **Swarm Intelligence**: Decentralized **Auction Protocols** for optimal task allocation and resource efficiency across agent collectives.
* **Multilayered Memory**: Research-grade memory hierarchy (STM → MTM → LTM) with intelligent context consolidation.
* **Interoperability**: Built with native **Model Context Protocol (MCP)** support for seamless tool and data integration.

---

## <span id="quick-start"></span> Quick Start

### 1. Prerequisites

* **Python**: 3.10+
* **Docker**: For Redis, Qdrant, and PostgreSQL infrastructure.
* **Vector/Relational Storage**: Managed via Docker Compose.

### 2. Installation

Install the core engine via pip:

```bash
pip install baselith-core
```

Or clone for extension development:

```bash
git clone https://github.com/baselithcore/baselithcore.git
cd baselith-core
docker compose up -d
```

### 3. Verification

```bash
baselith doctor  # Validate environment and configuration
```

---

## Resources

| Resource                                                                             | Description                                           |
| :----------------------------------------------------------------------------------- | :---------------------------------------------------- |
| [**Official Website**](https://baselithcore.xyz)                                     | The core landing page for the BaselithCore framework. |
| [**Official Documentation**](https://docs.baselithcore.xyz)                          | The official docs for the BaselithCore framework.     |
| [**Architecture**](https://docs.baselithcore.xyz/architecture/overview/)             | Deep dive into the "Sacred Core" and design choices.  |
| [**Plugin Guide**](https://docs.baselithcore.xyz/plugins/architecture/)              | How to extend BaselithCore using the plugin system.   |
| [**Agentic Patterns**](https://docs.baselithcore.xyz/architecture/agentic-patterns/) | Implementation of Agentic Design Patterns.            |
| [**Deployment**](https://docs.baselithcore.xyz/advanced/deployment/)                 | Production-ready deployment strategies.               |

---

## Contributing & License

We welcome contributions that adhere to our code standards. Please review [CONTRIBUTING.md](CONTRIBUTING.md).

BaselithCore is licensed under the **GNU Affero General Public License v3.0 (AGPL v3)**.
See [LICENSE](LICENSE) for full details.

---
Copyright © 2026 BaselithCore Team.
