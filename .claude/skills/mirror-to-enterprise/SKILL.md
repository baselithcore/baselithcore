---
name: mirror-to-enterprise
description: Procedure for mirroring core/ (and shared framework) changes from baselithcore-prod into the sibling baselithcore-enterprise checkout, docs included. Use whenever a change touches core/ or other shared, domain-agnostic machinery.
---

# Mirroring core/ changes to baselithcore-enterprise

The mandate lives in the root [CLAUDE.md](../../../CLAUDE.md) ("Companion enterprise repo"): every substantial change touching `core/` or other shared, domain-agnostic machinery **must** also land in `../baselithcore-enterprise`. The two `core/` trees must never drift. This skill is the reverse of the enterprise-side `mirror-to-prod` skill; it sequences the mandate and carries the commands.

## Scope

- **In scope**: `core/`, `scripts/check_*.py` gates, `migrations/`, `tests/unit/core/`, root config that both repos share (`pyproject.toml` core deps, `pytest.ini`, `.pre-commit-config.yaml`), and the matching `mkdocs-site/docs/` pages.
- **Out of scope, both directions**: this repo's `plugins/` are public-only; enterprise's `plugins/auth` and friends do not exist here. Never port an enterprise plugin back, and never assume a prod plugin exists there.
- A new `core/` symbol or seam is mirrored **even when enterprise has no consumer yet** — the point is that the trees stay identical.

## Procedure

1. **Diff before porting.** For every touched file:

    ```bash
    diff core/<file> ../baselithcore-enterprise/core/<file>
    ```

    - Diff shows only your change ⇒ enterprise was baseline-identical ⇒ safe byte-identical copy.
    - Diff shows unrelated divergence ⇒ enterprise is ahead on that file. Apply your change as a **patch**, preserving the enterprise-only additions. Reconcile, never overwrite.

2. **Port.** Copy or patch each file, then confirm the intended end state:

    ```bash
    diff core/<file> ../baselithcore-enterprise/core/<file>   # expect empty, or only enterprise-only extras
    ```

3. **Port the docs in the same pass.** Find the affected pages under `mkdocs-site/docs/` (`core-modules/*.md`, `architecture/*.md`, `advanced/*.md`) and mirror them. Code and docs ship together in both repos — no merge with stale docs. Do not carry prod-only plugin references into enterprise docs, or vice versa.

4. **Verify in the enterprise checkout** (run there, not here):

    ```bash
    cd ../baselithcore-enterprise
    python -m pytest tests/unit/core/<touched area> -q --no-cov
    python scripts/check_architecture_boundaries.py
    python scripts/check_file_size.py
    ruff check <ported files>
    ```

5. **Leave both trees uncommitted** for review. Commit or push in either repo only when the user asks.

## Red flags

- "It's a small core change, mirroring can wait" — the mandate has no size threshold; drift starts with one file.
- `cp -r core/ ../baselithcore-enterprise/core/` — a bulk copy silently destroys enterprise-only divergence. Diff per file.
- Porting code without its `mkdocs-site/docs/` counterpart.
- Claiming the mirror is done without running the gates **in the enterprise checkout**; green here proves nothing there.
