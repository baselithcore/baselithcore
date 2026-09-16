# Quality Gates

BaselithCore runs the same gates in three places. They are **nested supersets**,
not three separate configurations:

| Where | What runs | Scope |
| --- | --- | --- |
| `git commit` | the `pre-commit` stage | staged files, plus every whole-tree gate |
| `git push` | the `pre-push` stage | the whole tree, through the same hooks |
| CI | the `Quality Gates (pre-commit)` job | the whole tree, through the same hooks |

All three read one file: [`.pre-commit-config.yaml`](https://github.com/baselithcore/baselithcore/blob/main/.pre-commit-config.yaml).
CI does not invoke `ruff`, `mypy` or `bandit` itself — it runs
`pre-commit run --all-files`. There is no second copy of any argument list, so
there is nothing for the two to disagree about.

## Setup

```bash
pip install -e ".[dev]"
pre-commit install          # wires pre-commit, commit-msg AND pre-push
```

`pre-commit install` with no arguments is enough: the config declares
`default_install_hook_types`, so all three hook types are wired. A checkout
that skips this step gets no gates at all until CI.

## Why a commit can pass and the whole tree still fail

`git commit` hands the per-file hooks only what you staged. That is the point
of a commit hook — it keeps the edit-commit loop fast — and it means a commit
can be green while the tree is not: a formatting violation in a file you did
not touch, a stale plugin digest inherited from a rebase, a markdown file
someone else left unfixed.

`git push` closes that gap. The `full-tree` hook re-runs the whole set over
every file, which is byte for byte what CI will do. If your push passes, the
`Quality Gates` job passes.

Two kinds of hook behave differently at commit time, deliberately:

- **Whole-tree hooks** (`mypy-core`, `bandit`, and every `scripts/check_*.py`
  gate) declare `pass_filenames: false` and always examine the whole tree, even
  at commit time. They have to. mypy handed a single file cannot see the caller
  in another module that your signature change just broke — a per-file mypy
  hook is green on precisely the commits the pipeline rejects.
- **Per-file hooks** (ruff, prettier, markdownlint, the whitespace fixers) are
  scoped to the staged files at commit time and to everything at push time.

## Hooks that rewrite files

`ruff check --fix`, `ruff format`, `prettier --write` and `markdownlint --fix`
repair what they can. pre-commit treats a hook that modified the tree as a
**failure**: locally you re-stage and commit again, and in CI the job goes red
with `--show-diff-on-failure` printing exactly what the formatter would have
written. Formatting therefore needs no separate `--check` invocation anywhere.

Generated trees are excluded from every rewriting hook — `plugins/*/ui/{dist,out,build}`
and the exported `openapi.json`. Both are compared byte for byte by a gate
elsewhere (the UI build gate, the OpenAPI drift gate) and
`plugins/*/ui/dist/**` is inside the plugin integrity hash surface, so a single
reformatted byte breaks a signature.

## Running them by hand

```bash
pre-commit run --all-files              # everything CI runs
pre-commit run mypy-core --all-files    # one gate
pre-commit run --hook-stage pre-push    # what `git push` will do
SKIP=mypy-core pre-commit run --all-files   # everything but one gate
```

Run a gate's script directly (`python scripts/check_file_size.py`) for a faster
loop, but treat the hook as authoritative: the hook pins the interpreter and
the dependency set, and a bare `mypy core` in a fully populated virtualenv sees
different types from the pinned, minimal environment CI uses.

## What CI checks that a push cannot

Every job below needs something a git hook does not have — the full history,
the network, an importable app, a built artifact, or minutes rather than
seconds. This list is exhaustive: nothing else in CI can fail a change that a
clean `git push` accepted.

| Job | Needs | Checks |
| --- | --- | --- |
| `Secrets Scan (gitleaks)` | the full git history | a credential committed and later deleted still leaks; the hook only sees the staged diff |
| `Dependency Audit (pip-audit)` | the network | known vulnerabilities in the installed set and in the fully-resolved lock, with every extra the images ship |
| `Dependency Review` | the GitHub API | new dependencies introduced by a pull request |
| `Trivy Scan` | a built image and a CVE feed | OS and language packages in the container |
| `SBOM (CycloneDX)` | a resolved environment | the published bill of materials |
| `Lockfile Check (uv)` | a resolver | `uv.lock` still matches `pyproject.toml` |
| `Docs ↔ Code Consistency` | the app importable | the `--routes` pass — docs that claim a route or an import that must exist. The `--fast` half (paths, links, env vars, CLI) is a hook and runs locally |
| `OpenAPI Drift Gate` | the app importable | the committed spec equals what `create_app()` serves |
| `Client SDKs` | npm and a Python install | the hand-written clients build, type-check and test |
| `Eval Regression Gate`, `Red-Team Gate`, `Bias Examination Gate` | the eval corpora replayed | agent-flow contracts, adversarial cases, bias examination. The corpus *ratchet* is a hook; replaying the suites is not |
| `Package Smoke Test` | a built wheel and sdist | the distribution contains what it claims |
| `BaselithBot UI Build` | `npm ci` | the committed `ui/dist/` equals a clean rebuild |
| `Container Image Build` | Docker, multi-arch | the image builds on amd64 and arm64 |
| `Python Tests` | Postgres, Redis, Qdrant | the suite, on every supported Python, with the coverage gate |
| `Frontend JS Tests` | node | the shipped SSE client |
| `Helm Chart` | kubeconform | the chart renders to valid Kubernetes objects |
| `Workflow Lint (zizmor)` | — | the workflow definitions themselves |
| `Docs sync` (pull requests only) | the merge base | a changed `core/` module touched its docs page. Its commit-time half is the `docs-sync` **commit-msg** hook, which is also where the `[docs-sync: skip]` opt-out is read |

## Version pins

Tool versions live in `.pre-commit-config.yaml`. Three are duplicated on
purpose, and [`scripts/check_tool_pins.py`](https://github.com/baselithcore/baselithcore/blob/main/scripts/check_tool_pins.py)
— itself a hook — fails the build when they part ways:

- **ruff** and **mypy** are also in pyproject's `dev` extra, so `pip install -e
  ".[dev]"` gives a toolchain that agrees with the hooks when you invoke them
  by hand.
- **gitleaks** is also in `ci.yml`, because a full-history scan cannot run from
  a hook.
- **pre-commit** itself is pinned in `ci.yml` and must clear both the `dev`
  extra's floor and the config's `minimum_pre_commit_version`.

The gates run on **Python 3.12**, pinned by `default_language_version` in the
hook config and by the `quality_gates` job. mypy's verdict depends on the
version it runs under, so this is part of the gate rather than a build detail.

## Bypassing

`git commit --no-verify` and `git push --no-verify` skip every hook, including
the secret scanner. CI will not skip them. Prefer `SKIP=<hook-id>` when you
need to get past one specific gate, so the rest still run.
