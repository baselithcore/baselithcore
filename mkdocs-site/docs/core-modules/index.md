---
title: Core Modules
description: Core modules of the BaselithCore Framework
---

<!-- markdownlint-disable-file MD025 -->

---

## Module Categories

### Infrastructure

| Module                    | Description                                                      | Path                                             |
| ------------------------- | ---------------------------------------------------------------- | ------------------------------------------------ |
| **Dependency Injection**  | Service container and lifecycle management                       | [`baselith/core/di/`](di.md)                              |
| **Configuration**         | Centralized configuration with Pydantic                          | [`baselith/core/config/`](config.md)                      |
| **Middleware & Auth**     | Pure-ASGI middleware stack and auth dependencies                 | [`baselith/core/middleware/`](middleware.md)              |
| **Domain Models**         | Chat/domain models plus pricing, routing, and fallback           | [`baselith/core/models/`](models.md)                      |
| **Resilience**            | Circuit breakers, retry policies, bulkheads                      | [`baselith/core/resilience/`](resilience.md)              |
| **Registries & Errors**   | Generic thread-safe registry base and the exception hierarchy    | [`baselith/core/registries/`](registries.md)              |
| **Event System**          | Internal pub/sub for observability                               | [`baselith/core/events/`](events.md)                      |
| **Authentication & Auth** | Identity and access control management                           | [`baselith/core/auth/`](auth.md)                          |
| **Multi-Factor Auth**     | TOTP (RFC 6238) second factor and single-use recovery codes      | [`baselith/core/auth/mfa.py`](mfa.md)                     |
| **Webhooks**              | Signed, retried, dead-lettered outbound event delivery           | [`baselith/core/webhooks/`](webhooks.md)                  |
| **Connectors**            | One contract for external-system integrations: egress, retries, credentials, audited tools | [`baselith/core/connectors/`](connectors.md) |
| **Usage Quotas**          | Persistent per-key request budgets over daily/monthly windows    | [`baselith/core/quotas/`](quotas.md)                      |
| **Audit Trail**           | Durable, hash-chained audit records (AI Act Art. 12/19, GDPR Art. 5(2)) | [`baselith/core/observability/`](audit-trail.md)    |
| **Privacy & DSR**         | GDPR data-subject export/erasure and retention across providers  | [`baselith/core/privacy/`](privacy.md)                    |
| **Incident Reporting**    | NIS2 Art. 23 + DORA Art. 19 incident records with deadline tracking | [`baselith/core/incidents/`](incidents.md)            |
| **ICT Third-Party Register** | DORA Art. 28 Register of Information with Art. 29 concentration view | [`baselith/core/thirdparty/`](thirdparty.md)       |
| **AI Transparency**       | Art 50 AI-interaction disclosure and content provenance marking  | [`baselith/core/transparency/`](transparency.md)          |
| **AI Governance**         | AI system registry, Art. 5/6 classification, Annex IV, FRIA, ROPA | [`baselith/core/compliance/`](compliance.md)            |
| **Observability**         | Telemetry, tracing, and metric collection                        | [`baselith/core/observability/`](observability-module.md) |
| **NLP Utilities**         | Natural Language Processing tools and extractors                 | [`baselith/core/nlp/`](nlp.md)                            |
| **Caching System**        | Distributed cache management (Redis/memory)                      | [`baselith/core/cache/`](cache.md)                        |
| **Lifecycle Management**  | App and agent lifecycle coordination                             | [`baselith/core/lifecycle/`](lifecycle.md)                |
| **Real-time PubSub**      | Event-driven communication via Redis                             | [`baselith/core/realtime/`](realtime.md)                  |
| **Security & Encryption** | Field encryption at rest, pluggable secret resolution, unified SSRF guard | [`baselith/core/security/`](security.md)         |
| **Feature Flags**         | Runtime feature flags with percentage rollout and a pluggable backend | [`baselith/core/feature_flags/`](feature-flags.md)   |
| **Multi-Tenancy**         | Cross-tenant access guards and per-tenant encryption at rest     | [`baselith/core/tenancy/`](../advanced/multi-tenancy.md)  |
| **Utilities**             | Shared cosine similarity (numpy), token estimation (tiktoken), and the stdlib-only [runtime-environment resolver](config.md#runtime-environment) every fail-closed control reads | `baselith/core/utils/`                                    |

### Orchestration & Execution

| Module             | Description                                                          | Path                                        |
| ------------------ | -------------------------------------------------------------------- | ------------------------------------------- |
| **Orchestration**  | Intent classification, flow routing, RAG workflow handler            | [`baselith/core/orchestration/`](orchestration.md)   |
| **Prioritization** | Weighted task scoring, dependency graph and priority queue           | [`baselith/core/prioritization/`](prioritization.md) |
| **Workflows**      | Workflow builder, safe expression executor, LLM/retrieval/HTTP/classifier nodes, YAML definitions | [`baselith/core/workflows/`](workflows.md)           |
| **Workflow Catalog** | Directory-backed catalog, HTTP API, signed triggers, cron scheduling, workflows as MCP tools | [`baselith/core/workflows/`](workflow-catalog.md) |
| **Knowledge API**  | Ingest, delete and hit-test documents over HTTP; Q&A chunking | [`baselith/core/services/vectorstore/`](knowledge-api.md) |
| **Planning**       | Goal decomposition, dependency-aware step plans, budget constraints  | [`baselith/core/planning/`](planning.md)             |
| **Plugin System**  | Plugin discovery and lifecycle                                       | [`baselith/core/plugins/`](plugins.md)               |
| **Declarative Skills** | Versioned `SKILL.md` catalog surfaced with progressive disclosure | [`baselith/core/plugins/declarative.py`](skills.md) |
| **Agent API**      | Typed one-import `Agent`: Pydantic-validated output, plain-Python tools, automatic retry | [`baselith/core/agent/`](agent.md) |
| **Loop Engineering** | Verifier-owned iteration, futility detection, lesson compaction and escalation | [`baselith/core/loops/`](loops.md)      |
| **Task Queue**     | Distributed task scheduling with RQ                                  | [`baselith/core/task_queue/`](task-queue.md)         |
| **Chat & RAG**     | RAG-enabled chat workflows and historical context management         | [`baselith/core/chat/`](chat.md)                     |
| **Prompt Registry** | Versioned prompt templates with labels, A/B selection, and tracing  | [`baselith/core/prompts/`](prompts.md)               |

### Memory & Knowledge

| Module                  | Description                                     | Path                                                 |
| ----------------------- | ----------------------------------------------- | ---------------------------------------------------- |
| **Memory System**       | Multi-tier memory (Context, Graph, Vector)      | [`baselith/core/memory/`](memory.md)                          |
| **Hierarchical Memory** | Efficient STM/MTM/LTM context management        | [`baselith/core/memory/hierarchy.py`](hierarchical-memory.md) |
| **Knowledge Graph**     | L2 Structured entity relationships via FalkorDB | [`baselith/core/graph/`](graph.md)                            |
| **Services**            | LLM, VectorStore, Vision, Indexing, HITL        | [`baselith/core/services/`](services.md)                      |
| **Neutral Message API** | Provider-independent conversation history: tool-call correlation, `is_error`, verbatim thinking, stable cache prefix | [`baselith/core/services/llm/messages.py`](messages.md) |
| **Storage Layer**       | Typed Interaction/Feedback repositories with a PostgreSQL backend | [`baselith/core/storage/`](storage.md)      |
| **Supermemory**         | Cloud-native `MemoryProvider` with fact extraction, user profiles and hybrid search | [`baselith/core/memory/supermemory_provider.py`](supermemory.md) |
| **Database Layer**      | Relational database abstractions                | [`baselith/core/db/`](db.md)                                  |

### Agentic Intelligence & Reasoning

| Module                 | Description                                                | Path                                |
| ---------------------- | ---------------------------------------------------------- | ----------------------------------- |
| **Reasoning**          | Chain/Tree-of-Thought reasoning with shared MCTS utilities | [`baselith/core/reasoning/`](reasoning.md)   |
| **Reflection**         | Self-evaluation and refinement                             | [`baselith/core/reflection/`](reflection.md) |
| **Swarm Intelligence** | Multi-agent coordination with batch parallel execution     | [`baselith/core/swarm/`](swarm.md)           |
| **Guardrails**         | Input/output protection                                    | [`baselith/core/guardrails/`](guardrails.md) |

### Official Plugin Capabilities

These capabilities are part of the framework ecosystem, but their canonical implementation now lives in official plugins rather than the Sacred Core.

| Capability                | Description                                              | Path                                   |
| ------------------------- | -------------------------------------------------------- | -------------------------------------- |
| **Agent Plugins**         | Browser and coding agents with compatibility shims in `baselith/plugins/browser_agent/` and `baselith/plugins/coding_agent/` | [`Agent Plugins`](agents.md) |
| **Web Scraper Plugin**    | High-performance HTTpx/Playwright crawler in `baselith/plugins/web_scraper/` | [`Web Scraper`](scraper.md)   |
| **Document Sources**      | Readers, OCR backends, and ingestion adapters            | `baselith/plugins/document_sources/`            |
| **API Router Plugins**    | Application routers such as chat, feedback, and admin    | `baselith/plugins/api_routers/`                 |

### World & Exploration

| Module          | Description                                            | Path                                  |
| --------------- | ------------------------------------------------------ | ------------------------------------- |
| **World Model** | Internal representation and prediction of world state  | [`baselith/core/world_model/`](world-model.md) |
| **Exploration** | Autonomous information exploration and discovery       | [`baselith/core/exploration/`](exploration.md) |
| **Adversarial** | Adversarial scenario simulation and robustness testing | [`baselith/core/adversarial/`](adversarial.md) |

### Identity & Meta

| Module         | Description                                                           | Path                            |
| -------------- | --------------------------------------------------------------------- | ------------------------------- |
| **Personas**   | Configurable personalities and traits for agents                      | [`baselith/core/personas/`](personas.md) |
| **Meta-Agent** | Agent coordinating other agents and running multi-perspective debates | [`baselith/core/meta/`](meta.md)         |

### Learning & Evaluation

| Module                | Description                                                 | Path                                    |
| --------------------- | ----------------------------------------------------------- | --------------------------------------- |
| **Evaluation**        | LLM response quality evaluation via RAG metrics             | [`baselith/core/evaluation/`](evaluation.md)     |
| **Learning Loop**     | Continuous learning and evolutionary improvement of prompts | [`baselith/core/learning/`](learning.md)         |
| **Optimization Loop** | Feedback-driven automated prompt tuning                     | [`baselith/core/optimization/`](optimization.md) |
| **Auto Fine-Tuning**  | Automatic fine-tuning based on feedback and experiences     | [`baselith/core/finetuning/`](finetuning.md)     |
| **Skill Evolution**   | Pattern store, compiled declarative skills, validation gate with rollback | [`baselith/core/skill_evolution/`](skill-evolution.md) |

### Social & Integration

| Module                | Description                                             | Path                      |
| --------------------- | ------------------------------------------------------- | ------------------------- |
| **A2A Protocol**      | Agent-to-agent communication                            | [`baselith/core/a2a/`](a2a.md)             |
| **MCP Integration**   | Model Context Protocol for tools                        | [`baselith/core/mcp/`](mcp.md)             |
| **Human-in-the-Loop** | Human intervention for critical decisions and approvals | [`baselith/core/human/`](human.md)         |
| **Marketplace**       | Plugin discovery, install, validation, and publishing   | [`baselith/core/marketplace/`](marketplace.md) |

---

## Module Dependencies

```mermaid
graph TD
    Orch[Orchestration] --> DI[Dependency Injection]
    Orch --> Memory[Memory System]
    Orch --> Plugins[Plugin System]
    Orch --> RagWF[RAG Workflow]

    Plugins --> DI

    Memory --> Services[Core Services]
    Memory --> Utils[Utilities]
    Services --> DI

    Reasoning[Reasoning] --> Services
    Reasoning --> MCTSCommon[MCTS Common]
    Reflection[Reflection] --> Services
    Swarm[Swarm] --> Services
    WorldModel[World Model] --> MCTSCommon

    Planning[Planning] --> Services
    Planning -->|plan_to_workflow| Workflows
    Workflows[Workflows] --> DI

    Evaluation[Evaluation] -->|EVALUATION_COMPLETED| OptLoop[Optimization Loop]
    OptLoop --> Optimizer[PromptOptimizer]

    Guardrails[Guardrails] --> Services

    TaskQueue[Task Queue] --> DI
    Events[Event System] --> DI

    A2A[A2A Protocol] --> Services
    MCP[MCP] --> Services

    subgraph "Moved to plugins/"
        Browser[Browser Agent]
        Coding[Coding Agent]
        Scraper[Web Scraper]
        Docs[Document Sources]
        Routers[API Routers]
        Goals[Goals]
    end
```

---

## Design Principles

Each core module follows these principles:

### 1. Single Responsibility

Each module has one clear responsibility and doesn't overlap with others.

### 2. Protocol-Based Interfaces

All modules define `Protocol` interfaces for decoupling and testability.

### 3. Async-First

All I/O operations are asynchronous by default.

### 4. Dependency Injection

Modules receive dependencies via DI, never instantiate them directly.

### 5. Observability

All modules emit structured events for monitoring and debugging.

---

## Quick Navigation

Looking for something specific?

- **Getting Started**: [Dependency Injection](di.md) and [Configuration](config.md)
- **Request Handling**: [Orchestration](orchestration.md) and [Memory](memory.md)
- **Advanced AI**: [Reasoning](reasoning.md), [Reflection](reflection.md), [Swarm](swarm.md)
- **Integration**: [A2A Protocol](a2a.md), [MCP](mcp.md)
- **Operations**: [Task Queue](task-queue.md), [Resilience](resilience.md)
