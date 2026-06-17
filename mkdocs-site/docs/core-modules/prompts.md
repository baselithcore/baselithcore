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

## Storage

`PromptStore` is a pluggable Protocol; `InMemoryPromptStore` is the default. A
durable backend (Postgres) can be dropped in behind the same interface without
touching the registry or call sites.
