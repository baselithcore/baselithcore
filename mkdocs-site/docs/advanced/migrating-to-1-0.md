---
title: Migrating to 1.0
description: The one top-level package, how to update imports, the codemod and the compat package
---
<!-- ns-migrate: skip-file (this page quotes the pre-1.0 names on purpose) -->

BaselithCore 1.0 ships **one** top-level package, `baselith`. Before 1.0 the
`baselith-core` wheel installed three: `baselith`, `core` and `plugins`.

## Why the names moved

A top-level module name is unique per process, and `core` and `plugins` are
among the most common names a project has. A Django project with its own
`core` app, for example, could not use the 0.x wheel at all:

```text
myshop/
├── core/__init__.py      # the project's own Django app
└── run.py                # import django; django.setup(); from baselith import Agent
```

```text
ModuleNotFoundError: No module named 'core._version'
```

Whichever `core` came first on `sys.path` won, and there was no clean
workaround. Since 1.0 the framework owns only the name it is published under.

## What changed and what did not

| | Before 1.0 | Since 1.0 |
| --- | --- | --- |
| Public facade | `from baselith import Agent` | **unchanged** |
| Core modules | `from core.orchestration import Orchestrator` | `from baselith.core.orchestration import Orchestrator` |
| Bundled plugins | `from plugins.goals import GoalsPlugin` | `from baselith.plugins.goals import GoalsPlugin` |
| Plugin base class | `from core.plugins import Plugin` | `from baselith.core.plugins import Plugin` |
| Console script | `baselith` → `core.cli.__main__:main` | `baselith` → `baselith.core.cli.__main__:main` |
| Queue worker module | `python -m core.task_queue.worker` | `python -m baselith.core.task_queue.worker` |
| Logger names | `core.x`, `plugins.p.x` | `baselith.core.x`, `baselith.plugins.p.x` |
| Container paths | `/app/core`, `/app/plugins` | `/app/baselith/core`, `/app/baselith/plugins` |
| Migrations | `core/db/migrations` | `baselith/core/db/migrations`, inside the package |

If your code only imports from the facade (`from baselith import …`), it needs
no change. The import rewrite is mechanical, and two tools cover it: a codemod
that rewrites your code once, and a compat package that keeps the old names
working while you migrate.

The rename is not the only change in 1.0 that a running deployment notices:
MCP stdio servers now need an explicit allowlist, the MCP client refuses
compressed responses, and a few calls raise where they used to pass. Read
[Upgrade Notes](upgrade-notes.md) before rolling forward.

## Option 1: rewrite your code with the codemod

`scripts/migrate_namespace.py` is the same tool that moved this repository. It
rewrites, with `libcst`:

- every `from core… import` / `import core…` (and the same for `plugins`);
- dotted strings that name a module, such as `mock.patch("core.x.y")`,
  `monkeypatch.setattr`, `importlib.import_module`, `logging.getLogger` and RQ
  job paths;
- Python code fences in Markdown.

It is deterministic and idempotent: running it twice changes nothing the
second time. It is not part of the wheel, so take it from a checkout of the
BaselithCore repository at the 1.0 tag:

```bash
git clone --depth 1 --branch v1.0.0 https://github.com/baselithcore/baselithcore.git baselithcore-1.0
pip install "libcst>=1.5.0"
```

Then run it against your project:

```bash
cd my-project
git switch -c baselith-1.0        # work on a branch; review the diff before merging

# 1. Report only: what would change, and what it could not decide.
python ../baselithcore-1.0/scripts/migrate_namespace.py --root . --no-move --dry-run

# 2. Rewrite in place.
python ../baselithcore-1.0/scripts/migrate_namespace.py --root . --no-move --apply

git diff
```

**Always pass `--no-move` on your own project.** Without it the codemod also
performs the repository's directory move, `plugins/` → `baselith/plugins/`.
Your project's `plugins/` directory is a filesystem location the framework
reads plugins from, and it keeps its name: a plugin in `./plugins/weather/` is
still found there and now loads as `baselith.plugins.weather`.

Review the diff for two things the codemod cannot know about your project:

1. **Paths into your own plugin directory.** A string such as
   `Path("plugins/weather/data.json")` is rewritten to
   `baselith/plugins/weather/…`, which is right for the framework repository
   and wrong for yours. Revert those lines, and add `# ns-migrate: keep` to
   any line the codemod must leave alone on later runs.
2. **A project that has its own top-level `core` or `plugins` package.** The
   codemod treats every absolute `core` import as BaselithCore's. Such a
   project could not run 0.x either (see above); revert the rewrites that
   point at your own package.

A file carrying `ns-migrate: skip-file` anywhere in its text is not touched
at all.

The codemod's `--check` mode exits 1 while anything is left to rewrite. On a
project that keeps a `./plugins/` directory it also reports that directory as
a pending move, because `--check` does not honour `--no-move`. Read its summary
line, or use `--dry-run`, rather than gating CI on the exit code.

## Option 2: keep the old names with `baselith-core-compat`

`baselith-core-compat` is a separate, optional distribution. It is never part
of the main wheel, so it does not bring the name collision back.

```bash
pip install baselith-core-compat
```

Installed next to `baselith-core>=1.0`, it activates through a `.pth` hook,
with nothing to import. It then:

- aliases `core.*` to `baselith.core.*` and `plugins.*` to
  `baselith.plugins.*`. The aliases are the **same module objects**, not
  copies, so `isinstance` checks, registries and singletons keep working;
- emits one `DeprecationWarning` per legacy top-level name;
- steps aside when a real `core` or `plugins` package is importable: the
  project's own package wins, and its submodules are never hijacked.

It covers:

- third-party plugins published for 0.x (`from core.plugins import Plugin`,
  `from plugins.<self>.x import y`). They load through the 1.0 `PluginLoader`
  unchanged;
- RQ jobs queued by a 0.x worker with a `core.…` function path;
- scripts and notebooks you have not migrated yet.

Logger names are **not** aliased: see the operator notes below.

Treat the compat package as a bridge. Migrate with the codemod, then uninstall
it. `python -W error::DeprecationWarning` shows what still uses the old names.

## Plugin authors

- Rewrite the plugin's imports (Option 1), and declare the new floor in the
  manifest:

    ```yaml
    min_core_version: 1.0.0
    ```

- A plugin's `integrity_sha256` covers its sources, so re-sign it after the
  rewrite and bump its version.
- Until you publish a 1.0 release, your users can run the 0.x plugin under
  `baselith-core-compat`.

## Operators

Read these before upgrading a running deployment:

1. **Queued jobs.** Jobs enqueued by 0.x carry `core.…` function paths, and
   the dead-letter replay allowlist now defaults to
   `baselith.core.,baselith.plugins.`, so 0.x rows are refused (fail-closed).
   Drain every queue before upgrading. If you cannot, install
   `baselith-core-compat` and temporarily set
   `TASK_QUEUE_DLQ_REPLAY_ALLOWED_MODULES=baselith.core.,baselith.plugins.,core.,plugins.`
   until the old jobs are processed.
2. **Logs, dashboards and alerts.** Loggers are named after their modules.
   Filters, Loki and Sentry rules, dashboards, alerts and `log_config.yaml`
   overrides keyed on `core.` or `plugins.` must move to `baselith.core.` and
   `baselith.plugins.`.
3. **Plugins.** Every bundled plugin ships re-signed. A third-party 0.x plugin
   needs `baselith-core-compat` or a 1.0 release from its author.
4. **Database migrations.** Alembic revision ids are unchanged, so existing
   `alembic_version` rows stay valid. The migrations ship inside the package.
   A custom `alembic.ini` pointing at `core/db/migrations` must point at
   `baselith/core/db/migrations`; `baselith db migrate` already finds them.
5. **Containers and Helm.** Source lives at `/app/baselith/core` and
   `/app/baselith/plugins`. A volume mounting the repository's plugins
   (`./plugins:/app/plugins`) becomes
   `./baselith/plugins:/app/baselith/plugins`. User plugin directories are
   unchanged.
6. **Entry points.** The `baselith` command is unchanged. Anything calling a
   module directly (`python -m core.task_queue.worker`) must use the new name
   (`python -m baselith.core.task_queue.worker`).
