# Skills authoring

[← Index](./README.md)

Baselithbot skills follow the [OpenClaw authoring spec](https://docs.openclaw.ai/tools/skills)
so the same `SKILL.md` bundle runs both under the baselithbot plugin and
under an OpenClaw Gateway node, with ClawHub as the shared marketplace
endpoint. This document describes the bundle format, where to put files,
how the loader validates them, and how the dashboard composer writes
them.

## 1. Bundle layout

Each skill is a directory that contains at least `SKILL.md`. Optionally
a `MANIFEST.yaml` adds baselithbot/ClawHub quality signals.

```
<slug>/
├── SKILL.md         # required
├── MANIFEST.yaml    # optional — bundle_version + compatibility
└── …                # supporting files referenced via {baseDir}
```

## 2. Discovery roots (precedence order)

The loader ([`skills/loader.py`](../skills/loader.py)) scans the
following subdirectories of each configured root. First occurrence of a
given slug wins (OpenClaw precedence rules). Symlinked directories whose
resolved path escapes the root are rejected to block path traversal.

| Order | Subdirectory         | Intended use                      |
| ----- | -------------------- | --------------------------------- |
| 1     | `skills/`            | ClawHub/baselithbot default       |
| 2     | `.agents/skills/`    | OpenClaw workspace-local override |

Roots scanned by the plugin:

1. `state_dir/` — global baselithbot state (all workspaces).
2. `state_dir/workspaces/<name>/` — per-workspace override.

## 3. `SKILL.md` frontmatter

```yaml
---
name: Lead Qualifier              # required
description: Qualify inbound ICP  # required
version: 0.1.0                    # baselithbot extension (preserved)
tags: [sales, crm]                # baselithbot extension (preserved)

# OpenClaw-native optional fields
homepage: https://example.com/lead-qualifier
user-invocable: true
disable-model-invocation: false
command-dispatch: tool            # currently only "tool" recognized
command-tool: baselith.search
command-arg-mode: raw

metadata:
  openclaw:
    emoji: 🧠
    os: [darwin, linux]           # darwin|linux|win32
    primaryEnv: OPENAI_API_KEY
    skillKey: lead_qualifier
    always: false
    requires:
      bins: [ffmpeg]
      anyBins: [python, python3]
      env: [OPENAI_API_KEY]
      config: [agent.apiKey]
    install:
      - id: ffmpeg
        kind: brew               # brew|node|go|uv|download
        bins: [ffmpeg]
        os: [darwin, linux]
---

# When to use
…
# Instructions
…
# Output contract
…
```

Rules enforced during load:

- `name` and `description` non-empty → `errors` ⇒ status `invalid`.
- `command-dispatch=tool` requires `command-tool` → warning.
- Unknown `command-dispatch` value → warning.
- `metadata.openclaw.os` entries outside `{darwin, linux, win32}` →
  warning.
- All preserved frontmatter surfaces in `frontmatter` on
  `LocalSkillSpec`; the OpenClaw-native view is exposed via
  `LocalSkillSpec.openclaw` (snake_case).

## 4. `MANIFEST.yaml` (baselithbot extension)

```yaml
bundle_version: 0.1.0
compatibility:
  designed_for:
    surfaces: [chat, cli, ide]    # at least one recognized surface
  tested_on:
    - platform: baselithbot
      surface: chat
      model: claude-sonnet-4-6
      status: pass                # lowercase "pass" for credit
      date: 2026-04-20
tags: [sales, crm]
```

The `compatibility` block drives the validation tri-state surfaced on
the Skills dashboard:

- `verified` — no errors, no warnings.
- `provisional` — no errors, at least one warning (missing compat
  block, no passing `tested_on`, OpenClaw inconsistency, …).
- `invalid` — one or more structural errors; the skill is reported but
  not registered.

## 5. Authoring from the dashboard

The Skills page exposes an **Author custom skill** panel that writes a
validated bundle into the state tree without shell access. The composer
normalizes the slug (`[a-z0-9][a-z0-9_-]{1,62}`), enforces at least one
supported surface, emits a passing `tested_on` entry for every declared
surface, and optionally opens an **OpenClaw compatibility** subsection
for the native frontmatter fields (homepage, dispatch, requires,
install, …).

Behind the scenes:

- Endpoint: `POST /baselithbot/skills/workspace` (bearer-guarded +
  rate-limited via `skills_create`). Body schema:
  `WorkspaceSkillCreateRequest` in
  [`dashboard/schemas.py`](../dashboard/schemas.py).
- Writer: [`skills/writer.py`](../skills/writer.py) serializes
  frontmatter + manifest and refuses to overwrite unless the request
  sets `overwrite: true` → 409.
- Rescan: the plugin invokes `rescan_workspace_skills()` so the
  in-memory registry reflects the bundle immediately.

## 6. BaselithCore alignment

- **Sacred Core rule** — authoring lives under `plugins/baselithbot/`;
  no `core → plugins` imports are added.
- **File size cap (500 LOC)** — [`loader.py`](../skills/loader.py) and
  [`writer.py`](../skills/writer.py) stay under the cap.
- **Typed + async** — every schema is Pydantic; route handlers are
  async.
- **Validated boundaries** — slug regex, surface enum, OpenClaw
  `command-dispatch` enum, OS enum, max-length caps on every string.
- **Audit + bus** — each create fires `skill.workspace_created` on the
  dashboard event bus.

## 7. Future work

- Accept `.zip`/tarball uploads and unpack into the same bundle layout.
- Render diff UI when `overwrite=true` replaces an existing bundle.
- Add `install` spec editor with per-kind (brew/node/go/uv/download)
  forms rather than free-form JSON.
