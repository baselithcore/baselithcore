---
title: Architecture
description: Overview of the BaselithCore architecture
---



This section describes the architecture of BaselithCore, its components, and the design patterns used.

---

## Documentation

<div class="feature-grid" markdown>

<div class="feature-card" markdown>

### :material-sitemap: Overview

General overview of the layered architecture and design principles.

[:octicons-arrow-right-24: Read](overview.md)

</div>

<div class="feature-card" markdown>

### :material-transit-connection: Request Flow

The complete flow of a request through the system.

[:octicons-arrow-right-24: Read](request-flow.md)

</div>

<div class="feature-card" markdown>

### :material-brain: Agentic Patterns

The agentic patterns implemented in the framework.

[:octicons-arrow-right-24: Read](agentic-patterns.md)

</div>

</div>

---

## Fundamental Principles

!!! abstract "Core Design Principles"

    1. **Plugin-First Architecture** - Domain logic lives in plugins, never in core
    2. **Async by Default** - All I/O operations are asynchronous
    3. **Dependency Injection** - Services are injected, never instantiated directly
    4. **Protocol-Based Interfaces** - Decoupling through Python protocols
    5. **Resilience by Design** - Circuit breaker, retry, rate limiting integrated

---

## Architecture Goals

The framework architecture is designed to achieve:

### Modularity

Clean separation between infrastructure (core) and application logic (plugins), enabling independent development and deployment.

### Scalability

Horizontal scaling through distributed workers, multi-tier storage, and stateless orchestration.

### Observability

Built-in instrumentation with OpenTelemetry, structured logging, and event-driven metrics collection.

### Security

Defense-in-depth with input validation, output sanitization, multi-tenancy isolation, and secrets management.

### Developer Experience

Clear contracts through Protocols, comprehensive CLI tooling, and auto-generated API documentation.
