# Declarative Skills

Plugins can ship **declarative skills**: versioned Markdown instruction
sets (`SKILL.md`) the agentic loop surfaces to the model with
**progressive disclosure** — only the lightweight catalog (name +
description) enters the system prompt; the full body is loaded on demand
when the model activates a specific skill. This scales to 50+ skills
without context explosion.

Skills can also be *generated from experience*: the
[Skill Evolution](skill-evolution.md) loop distills run outcomes into
persistent patterns and compiles recurring ones into managed skills
served through this same catalog.

## Authoring a skill

Convention: a plugin exposes skills by shipping a `skills/` directory at
its top level, one subdirectory per skill:

```text
plugins/<name>/
└── skills/
    └── structured-summary/
        ├── SKILL.md
        ├── scripts/       # optional .py helpers (see Bundled files below)
        ├── references/    # optional supporting documents
        └── assets/        # optional templates/binary assets
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
   `memory_context` and the other capabilities. The reasoning handler
   renders the catalog **query-aware**: `render_catalog(query=...)`
   pre-filters a large catalog by BM25 relevance (see below).
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

## Catalog relevance pre-filter

LLM-in-prompt skill selection degrades once the catalog grows past ~50
entries, so `render_catalog(query=...)` pre-filters large catalogs: when a
query is provided **and** the catalog exceeds
`SKILL_CATALOG_PREFILTER_THRESHOLD` (default `50`, env
`BASELITH_SKILL_CATALOG_PREFILTER_THRESHOLD`), only the
`SKILL_CATALOG_PREFILTER_TOP_K` (default `25`, env
`BASELITH_SKILL_CATALOG_PREFILTER_TOP_K`) most relevant cards — BM25 over
`name + description`, the card text the model would see anyway — are
rendered, with a one-line note that the catalog was filtered. Without a
query, or under the threshold, output is **byte-identical** to the
unfiltered render. When fewer than top-k cards score positively, the
remainder is padded with the name-sorted rest, so an off-vocabulary query
never collapses the catalog to nothing. Malformed or non-positive env
overrides fall back to the defaults with a warning.

## Bundled files: scripts, references, assets

A skill directory may ship supporting files in three same-named
subdirectories; `DeclarativeSkillLoader.activate()` enumerates them onto the
`LoadedSkill` (`scripts`, `references`, `assets` — sorted POSIX-style paths
relative to each subdirectory; empty lists when a subdirectory is absent).
Every enumerated file is sandbox-validated against the loader roots, so a
symlink escaping the roots fails the activation with `SkillSandboxError`.

### Running bundled scripts (`run_skill_script`)

`core.plugins.skill_scripts.run_skill_script` executes a `.py` helper from an
activated skill's `scripts/` directory non-interactively — *model proposes,
code disposes*: the model only ever names a skill and a relative script
filename, resolution and validation happen in code.

```python
from core.plugins import run_skill_script

result = await run_skill_script(loader, "code-review", "lint.py", ["--fix"])
result.exit_code     # negative signal number when killed (e.g. timeout)
result.stdout        # capped at 64KB with a truncation marker
result.stderr        # capped at 64KB; carries a timeout marker when killed
result.parsed_json   # json.loads of stdout when it is valid JSON, else None
```

Guarantees, all enforced in `_resolve_script_path` / the runner:

* **Path containment** — absolute paths, `..` traversal, and symlink escapes
  out of the skill's `scripts/` directory raise `SkillSandboxError`; only
  `.py` files run (via `sys.executable`), unknown skills/scripts raise
  `ValueError`.
* **Non-interactive** — stdin is closed (`DEVNULL`); the working directory is
  the skill directory.
* **Bounded** — stdout and stderr are each capped at 64KB
  (`MAX_OUTPUT_CHARS`), and the process is **killed** after `timeout_s`
  (default `30.0`).

`make_run_skill_script_tool(loader)` packages this as a `run_skill_script`
`ToolDefinition` for tool registries (the callable returns a JSON report as
plain text, or an `Error:` string). It is registered with autonomy category
**`mutating`** — bundled scripts execute code, so the
[`AutonomyPolicy`](orchestration.md#autonomypolicy-three-tier-spectrum)
approval gates apply.

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
