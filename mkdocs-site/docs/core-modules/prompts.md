---
title: Prompt Registry
description: Versioned prompt templates with labels, A/B selection, and tracing linkage
---

The `core/prompts` registry treats prompts as **versioned, content-addressed
artifacts** rather than strings scattered through the code. Register many
versions of a named prompt, point labels (`production`, `staging`) at a version,
render with variables, and run deterministic A/B experiments — all while
carrying the resolved name+version into traces so output can be grouped by the
exact prompt that produced it.

It complements the [prompt *builder*](chat.md) (`core/chat/prompt_engine.py`):
the builder assembles a prompt from layers and few-shot examples; the registry
*stores, versions, resolves, and renders* named templates.

## Concepts

- **`PromptVersion`** — one immutable version: `name`, `version`, `template`,
  `labels`, declared `variables`, and a content `checksum`.
- **Label** — a moving pointer (`production` → `name@2`) so callers resolve "the
  current production prompt" without pinning a version.
- **`RenderedPrompt`** — the rendered text plus `name`/`version`/`checksum` and
  the variables used; exposes `span_attributes()` for tracing.

## Registering & resolving

```python
from core.prompts import get_prompt_registry

reg = get_prompt_registry()
reg.register("greet", "Hello {{ name }}, welcome to {{ product }}.",
             version="1", labels={"production"}, variables=["name", "product"])
reg.register("greet", "Hi {{ name }}! ({{ product }})", version="2")

reg.get("greet")                      # latest registered → v2
reg.get("greet", version="1")         # explicit version
reg.get("greet", label="production")  # by label → v1
reg.promote("greet", "2", "production")  # move the label to v2
```

## Rendering

Templates use `{{ variable }}` placeholders. Substitution is a **literal string
replacement** — there is no expression evaluation, attribute access, or
format-spec handling (unlike `str.format`/f-strings), so neither a template nor
a variable value can reach code execution or leak object internals. Missing
variables raise in strict mode (the default).

```python
rendered = reg.render("greet", {"name": "Gio", "product": "Baselith"}, label="production")
rendered.text                # "Hello Gio, welcome to Baselith."
rendered.span_attributes()   # {"prompt.name": "greet", "prompt.version": "1", ...}
```

### Online evaluation / tracing

Attach `rendered.span_attributes()` to the LLM call's OpenTelemetry span (or your
evaluation record). Traces and evals can then be sliced by `prompt.name` +
`prompt.version`, which is the basis for measuring a prompt change's effect in
production.

## A/B experiments

`select_variant` buckets a stable subject (tenant/user/session) across weighted
versions using the same deterministic hashing as
[feature flags](feature-flags.md) — the same subject always sees the same
variant, so an experiment is consistent per subject.

```python
variant = reg.select_variant("greet", subject=user_id, weights={"1": 50, "2": 50})
reg.render("greet", vars, version=variant.version)
```

[Catalog prompts](#packaged-catalog-prompts) can switch this on via env alone —
see [Env-driven A/B experiments](#env-driven-ab-experiments).

## File-based prompts

Keep prompts as reviewable, diff-friendly Markdown files with YAML front matter
and load a whole directory at startup:

```markdown
---
name: greet
version: "2"
labels: [production]
variables: [name, product]
---
Hello {{ name }}, welcome to {{ product }}.
```

```python
from core.prompts import get_prompt_registry, load_prompts_from_dir

load_prompts_from_dir(get_prompt_registry(), "prompts/")
```

Malformed files are logged and skipped — one bad file never blocks the rest.

**Env autoload:** set `BASELITH_PROMPTS_DIR=<dir>` and the global
`get_prompt_registry()` loads the catalog automatically on first use — no
wiring code needed per deployment.

**Trace linkage:** every `registry.render()` emits a `prompt.render <name>`
span carrying `prompt.name` / `prompt.version` / `prompt.checksum`, so LLM
spans in the same trace can be grouped by prompt version — the foundation of
online prompt evaluation and A/B analysis.

## Packaged catalog prompts

`core/prompts/catalog.py` generalizes the pattern pioneered by the
conversation system prompt (`core/chat/prompt.py`): the framework's own
hot-path prompts ship as Markdown catalog files under `core/prompts/catalog/`
(packaged in the wheel) and are served through the registry instead of
hardcoded `.format` strings — versioned, label-resolved, and traced.

```python
from core.prompts.catalog import resolve_catalog_prompt

prompt = resolve_catalog_prompt(
    "react_system",
    {"tool_descriptions": "- search: Search the web", "max_iterations": 5},
    fallback_template="Use at most {{ max_iterations }} tool calls.\n"
    "Available tools:\n{{ tool_descriptions }}",
)
```

`resolve_catalog_prompt(name, variables, *, catalog_file=None,
fallback_template=None, label="production", subject=None)` works as follows:

- **Seeding** — on first use, if nothing is registered under `name`, the
  packaged `<name>.md` file is parsed and put into the global registry. A
  deployment catalog (`BASELITH_PROMPTS_DIR`) or programmatic registration
  registers first and therefore **wins over the packaged default** — override
  a framework prompt by shipping a file with the same `name`.
- **Resolution** — an [env-configured A/B variant](#env-driven-ab-experiments)
  first (when weights are set for this prompt), then the `production` label,
  then latest registered version, then the caller's embedded
  `fallback_template` (registry unavailable / file missing). Without a
  fallback, failures propagate.
- **Provenance** — registry renders emit the `prompt.render` span, so LLM
  spans are attributable to the exact prompt name/version/checksum.

Five framework prompts are catalog-served today:

| Prompt name | Call site |
|-------------|-----------|
| `react_system` | `core/reasoning/react.py` (text-parsing ReAct loop) |
| `react_native_system` | `core/reasoning/react_native.py` (native tool-calling loop) |
| `intent_classification` | `core/orchestration/intent_classifier.py` (`build_classification_prompt`) |
| `swarm_decomposition` | `core/orchestration/handlers/swarm_agents.py` (`build_decomposition_prompt`) |
| `loop_goal_hardening` | `core/loops/goal.py` (`harden_goal`, pre-flight goal questionnaire) |

!!! note "Literal braces survive"
    Catalog templates use `{{ var }}` placeholders; the renderer matches
    **only** `{{ identifier }}`, so literal JSON examples in a prompt keep
    plain single braces (`{ "intent": ... }`) untouched — no doubling-up as
    with `str.format`.

### Env-driven A/B experiments

`resolve_catalog_prompt` folds the registry's [A/B selection](#ab-experiments)
into the catalog path, so a live experiment on a hot-path prompt needs **no
code change**. Set `BASELITH_PROMPT_VARIANTS_<NAME>` — the prompt name
uppercased, non-alphanumerics replaced by underscores — to a
`"version:weight,..."` string and the prompt resolves through `select_variant`
instead of the label path:

```bash
# 50/50 split of react_system versions 1 and 2, stable per tenant
BASELITH_PROMPT_VARIANTS_REACT_SYSTEM=1:50,2:50
```

The bucketing `subject` defaults to the ambient tenant
(`core.context.get_tenant_or_default()`) — each tenant sees one stable
variant — or pass `subject=` explicitly for user/session-level bucketing:

```python
prompt = resolve_catalog_prompt(
    "react_system",
    {"tool_descriptions": "- search: Search the web", "max_iterations": 5},
    subject="user-42",   # bucket per user instead of per tenant
)
```

The experiment path **fails back, never breaks serving**: a malformed weight
string and weights whose versions cannot be resolved in the registry are
logged (`prompt_variants_env_malformed` /
`prompt_variant_unresolved_falling_back_label`) and resolution continues down
the normal label path. Variant renders emit the same `prompt.render` span, so
traces slice by the exact version each subject received — the measurement side
of the experiment.

## Storage

`PromptStore` is a pluggable Protocol; `InMemoryPromptStore` is the default.
Beyond version put/get and label resolution, the store answers the
catalog-inspection queries the admin API needs: `names()` (registered prompt
names, first-registration order) and `labels(name)` (label → version mapping,
empty when none).

Reads stay in-memory in every configuration — rendering a prompt never does
I/O. Durability is layered *behind* the store by the synchronizer below, not
by swapping the store out.

## Durable catalog and cross-replica sync

The in-memory store makes runtime label promotion **replica-local**: a
`promote()` on one replica never reaches the others, so behind a 2+-replica
deployment "the production prompt" could differ per replica.
`PromptSynchronizer` (`core/prompts/sync.py`) closes that gap without
changing the registry's synchronous contract:

- **Writes** (`push_version` / `push_label`) go through a durable backend
  *and* the local registry store (write-through). A backend write error
  **raises** — the caller must know a promotion did not persist.
- **Reads** stay in-memory — zero per-render I/O.
- A per-replica **refresh loop** periodically imports versions and labels
  written elsewhere, so every replica converges within the refresh interval
  (default `30.0` s). The refresh is fail-open: a backend error logs
  (`prompt_sync_refresh_failed`) and the next tick retries.

`push_label` validates that `name@version` is registered locally and raises
`PromptNotFoundError` otherwise — a label can never point at a version the
catalog does not hold.

```python
from core.prompts.store_postgres import PostgresPromptBackend
from core.prompts.sync import PromptSynchronizer
from core.prompts.types import PromptVersion

backend = PostgresPromptBackend()
await backend.initialize()          # idempotent DDL

sync = PromptSynchronizer(backend=backend, interval_seconds=30.0)
await sync.start()                  # initial refresh + background loop

await sync.push_version(
    PromptVersion(name="greet", version="3", template="Hey {{ name }}!")
)
await sync.push_label("greet", "production", "3")   # durable promote
```

`PostgresPromptBackend` (`core/prompts/store_postgres.py`) implements the
`PromptBackend` Protocol on two tables — `prompt_versions` (primary key
`(name, version)`) and `prompt_labels` (primary key `(name, label)`) — created
idempotently (`CREATE TABLE IF NOT EXISTS`) on `initialize()`, mirroring the
checkpoint store's approach. Any other durable backend can implement the same
four-method Protocol (`initialize` / `upsert_version` / `set_label` /
`fetch_all`); a backend for a store the core does not already speak belongs
in a plugin.

### Enabling it (env)

Prompt sync is **opt-in** and wired by the app lifespan
(`core.api._runtime_services` → `start_prompt_sync_from_env`), fail-open at
startup — a backend that cannot start logs a warning and the app boots with a
replica-local catalog:

```env
BASELITH_PROMPT_SYNC=postgres      # unset (default) = replica-local prompts
BASELITH_PROMPT_SYNC_INTERVAL=30   # refresh interval, seconds (default 30.0)
```

When enabled, the process-wide instance is reachable via
`core.prompts.sync.get_prompt_synchronizer()` (returns `None` when sync is
off) — which is how the admin API below decides whether writes are safe.

### Admin API (`/prompts`)

The `api_routers` plugin mounts an operator surface for the catalog
(`plugins/api_routers/prompts.py`), protected by the same **admin HTTP Basic
Auth** as the admin dashboard. Reads always serve the local registry; the
write endpoints refuse with **503** when no synchronizer is configured,
because a promotion that silently stays replica-local is a footgun.

| Method & path | Description |
| ------------- | ----------- |
| `GET /prompts` | Names, versions and labels of every registered prompt (always local) |
| `POST /prompts/{name}/versions` | Register **and persist** a new version (`201`; `503` without sync) |
| `POST /prompts/{name}/labels/{label}` | Durable label promote (`404` unknown version; `503` without sync) |

```bash
curl -u admin:password http://localhost:8000/prompts

curl -u admin:password -X POST http://localhost:8000/prompts/greet/versions \
  -H "Content-Type: application/json" \
  -d '{"version": "3", "template": "Hey {{ name }}!", "variables": ["name"]}'

curl -u admin:password -X POST \
  http://localhost:8000/prompts/greet/labels/production \
  -H "Content-Type: application/json" \
  -d '{"version": "3"}'
```

A version registered here reaches every other replica on its next refresh
tick; the label promote is the runtime end of the same lever the
[env-driven A/B experiments](#env-driven-ab-experiments) pull at deploy time.
Endpoint details are in the
[REST API reference](../api/rest.md#prompt-catalog-administration).
