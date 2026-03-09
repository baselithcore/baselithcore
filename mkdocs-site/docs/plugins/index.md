---
title: Plugin Development
description: Guide to developing plugins for BaselithCore
---

<!-- markdownlint-disable-file MD046 MD025 -->

This section covers all aspects of plugin development.

---

## Documentation

<div class="feature-grid" markdown>

<div class="feature-card" markdown>

### :material-puzzle: Architecture

Plugin anatomy and capability mixins.

[:octicons-arrow-right-24: Read](architecture.md)

</div>

<div class="feature-card" markdown>

### :material-hammer-wrench: Creating Plugins

Step-by-step tutorial for creating plugins.

[:octicons-arrow-right-24: Read](creating-plugins.md)

</div>

<div class="feature-card" markdown>

### :material-transit-connection: Flow Handlers

Sync and stream handlers for intents.

[:octicons-arrow-right-24: Read](flow-handlers.md)

</div>

<div class="feature-card" markdown>

### :material-monitor: Frontend Integration

Extend the UI without recompiling.

[:octicons-arrow-right-24: Read](frontend-integration.md)

</div>

<div class="feature-card" markdown>

### :material-store: Marketplace

Publish and distribute plugins.

[:octicons-arrow-right-24: Read](marketplace.md)

</div>

<div class="feature-card" markdown>

### :material-package: Packaging

Package plugins for distribution.

[:octicons-arrow-right-24: Read](packaging.md)

</div>

<div class="feature-card" markdown>

### :material-brain: Reasoning Agent

Advanced cognitive capabilities with Tree of Thoughts (ToT).

[:octicons-arrow-right-24: Read](reasoning-agent.md)

</div>

</div>

---

## Quick Reference

### Plugin Structure

```text
plugins/my-plugin/
├── plugin.py          # Entry point (required)
├── agent.py           # Agent logic
├── handlers.py        # Flow handlers
├── router.py          # API endpoints
├── static/            # Frontend assets
└── README.md          # Documentation
```

### Capability Mixins

| Mixin          | Use                         |
| -------------- | --------------------------- |
| `AgentPlugin`  | Exposes agents and handlers |
| `RouterPlugin` | Exposes API endpoints       |
| `GraphPlugin`  | Extends knowledge graph     |

### CLI Commands

```bash
# Create plugin (interactive wizard)
baselith plugin create --interactive

# List and status with readiness badges
baselith plugin list
baselith plugin status

# Advanced diagnostics
baselith plugin deps check <name>
baselith plugin tree

# Development tools
baselith plugin logs <name> --follow
baselith plugin validate <name>
```
