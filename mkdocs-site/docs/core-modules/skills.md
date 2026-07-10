# Declarative Skills

Plugins can ship **declarative skills**: versioned Markdown instruction
sets (`SKILL.md`) the agentic loop surfaces to the model with
**progressive disclosure** — only the lightweight catalog (name +
description) enters the system prompt; the full body is loaded on demand
when the model activates a specific skill. This scales to 50+ skills
without context explosion.

## Authoring a skill

Convention: a plugin exposes skills by shipping a `skills/` directory at
its top level, one subdirectory per skill:

```text
plugins/<name>/
└── skills/
    └── structured-summary/
        └── SKILL.md
```

`SKILL.md` is Markdown with YAML frontmatter:

```markdown
---
name: structured-summary
description: Produce a structured executive summary from retrieved documents.
version: 0.1.0
requires_approval: false
tools: []
---

# Structured summary

Step-by-step instructions the model receives on activation…
```

Frontmatter contract (validated by
`core.plugins.declarative.DeclarativeSkillLoader`):

| Field | Required | Constraint |
| --- | --- | --- |
| `name` | yes | non-empty string, ≤ 80 chars, unique across the deployment |
| `description` | yes | non-empty string, ≤ 200 chars (this is all the model sees pre-activation — make it decisive) |
| `version` | no | string |
| `requires_approval` | no | boolean; `true` gates activation through human-in-the-loop |
| `tools` | no | list of tool names the skill's instructions rely on |

A malformed `SKILL.md` disables that plugin's skills (with a warning) —
never the whole catalog. On duplicate names the first provider (plugins
sorted by name) wins and the duplicate is logged.

See [plugins/example-plugin/skills/structured-summary/SKILL.md](https://github.com/baselithcore/baselithcore/blob/main/plugins/example-plugin/skills/structured-summary/SKILL.md)
for the reference skill.

## Runtime flow

`core.plugins.skills_service.SkillService` aggregates every plugin's
skill root (via `PluginRegistry.get_all_skill_roots()`, cached with a
30 s TTL) and the orchestrator wires it into the loop:

1. **Catalog injection** — `ExecutionMixin` places the service on the
   request context (`context["skill_service"]`) plus a prompt-ready
   Markdown catalog (`context["skills_catalog"]`), alongside
   `memory_context` and the other capabilities.
2. **Activation tool** — the reasoning handler exposes
   `activate_skill(<name>)` to both execution strategies:
   * **ReAct** gets an extra `ToolDefinition` and the catalog appended to
     its system prompt; the loaded body arrives as the tool observation.
   * **`parallel_tools`** gets `activate_skill` registered in the tool
     registry (unless the caller already provided one), so the same
     autonomy/budget/contract gates apply.
3. **Envelope** — `SkillService.activate()` returns the canonical
   [`SkillResult`](plugins.md) envelope: `ok` with
   `{name, plugin, version, tools, body}`, `partial`
   (`skill_tools_unavailable`) when declared `tools` are missing from the
   current run, `fail` otherwise (`skill_not_found`,
   `skill_approval_required`, `skill_approval_denied`,
   `skill_load_error`).

## Safety posture

* **Sandboxed reads** — every load re-validates the path against the
  discovered plugin skill roots (`SkillSandboxError` on escape): *model
  proposes, code disposes*.
* **Signed bodies** — `SKILL.md` files are part of the plugin integrity
  surface (`integrity_sha256`): a tampered skill body fails
  `verify_plugin_integrity` just like tampered source. Re-sign after
  editing a skill: `python scripts/sign_changed_plugins.py`.
* **Approval gate** — `requires_approval: true` routes activation through
  `core.human.HumanIntervention` and **fails closed** when no approval
  channel is configured.
* **Injection scan** — activated bodies pass the indirect-injection
  scanner (`core.guardrails.scan_external_content`, detection-first)
  before reaching the model.
* **Telemetry** — every activation emits a `skill.activate` span with
  `gen_ai.operation.name=execute_tool` and
  `gen_ai.baselith.skill_name`/`skill_plugin` attributes.

## Relationship to plugin-local skill systems

The `baselithbot` plugin keeps its richer, OpenClaw-compatible skill
subsystem (ClawHub marketplace, workspace scopes, MANIFEST.yaml quality
signals) but reuses the core frontmatter parser
(`core.plugins.declarative.split_frontmatter`), so `SKILL.md` frontmatter
semantics stay uniform across the stack. New plugins should prefer the
declarative `skills/` convention and only build custom machinery for
genuinely domain-specific needs.
