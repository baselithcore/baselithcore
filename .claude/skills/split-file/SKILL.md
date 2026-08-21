---
name: split-file
description: Use when a source file approaches or exceeds 500 lines, when check_file_size.py fails, when planning a feature whose natural home file is already large, or when tempted to write one big module and split it later.
---

# Splitting files under the 500-line cap

The cap is a hard limit — physical lines, blanks and comments included — across `.py`, `.ts`, `.tsx`, `.js`, `.jsx` and `.vue`, tests included. Only `templates/` and `backstage-portal/` are out of scope. It applies from the first commit: design the module layout before writing, never plan a monolith-then-split.

Gate: [scripts/check_file_size.py](../../../scripts/check_file_size.py) (pre-commit hook `file-size-cap` + the Architecture Boundaries CI job). The ratchet baseline [scripts/file_size_baseline.json](../../../scripts/file_size_baseline.json) freezes pre-existing over-cap files at their current length — **it is currently empty, meaning this repo has zero over-cap files. Never add an entry.** A new entry is a regression, not a waiver.

## Split strategies, in order of preference

1. **Package extraction** — `foo.py` becomes `foo/{__init__.py, _core.py, _helpers.py, _types.py}`, with `__init__.py` re-exporting the public surface so importers do not change. Frontend: a fat component becomes `Component/{index.tsx, hooks.ts, parts/*.tsx, types.ts}`.
2. **Sibling extraction** — move pure helpers into `utils.py` / `types.py` (`utils.ts` / `types.ts`).
3. **Responsibility seams** — cut handlers, routers and agents along parsing vs. orchestration vs. I/O vs. persistence; UI along presentation vs. state vs. data-fetching.

`core/api/lifespan.py` is the worked example in this repo: the mount and lazy-activation callbacks were extracted to `core/api/_plugin_runtime.py` as methods on `PluginRuntimeHooks`, same behaviour, same call sites.

## Procedure

1. Baseline the behaviour: the relevant tests are green **before** the split.
2. Cut along seams, preserving the public import surface via `__init__.py` re-exports. Mechanical move only — no behaviour change in the same commit. When re-exports cannot preserve the surface (circular imports, changed registration paths), update every caller in the same commit and say so in the PR; never leave the old and new surfaces both alive.
3. Every new module gets an `__init__.py` with explicit exports (repo convention).
4. Re-run the same tests plus `python scripts/check_file_size.py`.
5. Was the file in the baseline? `python scripts/check_file_size.py --update-baseline` removes the entry once it is under the cap.
6. File in a plugin? The `.py` moves change the integrity surface — the `sign-changed-plugins` pre-commit hook re-signs; bump the manifest `version` (patch) manually.
7. Split touched `core/`? The `mirror-to-enterprise` skill applies.

## Red flags

- "Just this once over the cap" / "I'll split after the PR" — split before the PR, no exceptions.
- Adding a `file_size_baseline.json` entry for a file you just wrote.
- Splitting by line count instead of cohesion (`part1.py`, `part2.py`) — cut along responsibility seams and keep the names semantic.
- Behaviour changes smuggled into the split commit.
- A JSON or YAML catalog over 500 lines: the script only enforces `.py .ts .tsx .js .jsx .vue`, so it escapes the gate — not the policy. Split it by feature or namespace anyway.
