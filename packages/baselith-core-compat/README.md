<!-- ns-migrate: skip-file -->
# baselith-core-compat

BaselithCore 1.0 moved its implementation under a single top-level package:

| Before 1.0 | Since 1.0 |
| --- | --- |
| `from core.orchestration import Orchestrator` | `from baselith.core.orchestration import Orchestrator` |
| `from plugins.goals import GoalsPlugin` | `from baselith.plugins.goals import GoalsPlugin` |

`from baselith import Agent` (the public facade) did not change.

Install this optional package to keep the old names importable while code
migrates:

```bash
pip install baselith-core-compat
```

It aliases `core.*` and `plugins.*` to the new modules (the same module
objects, not copies) and emits one `DeprecationWarning` per legacy name. It
covers plugins published before 1.0, RQ jobs already queued with a `core.…`
function path, and scripts that have not been migrated.

It never claims the names first: a real `core` or `plugins` package on
`sys.path` (a Django app called `core`, say) is imported as usual, and the
aliases apply only when nothing else answers.

Logger names are not aliased: code under `baselith.core` logs as
`baselith.core.…`. Update log filters, dashboards and alerts that match on
`core.`.

Migrate code for good with the codemod shipped in the BaselithCore repository,
then uninstall this package:

```bash
python scripts/migrate_namespace.py --root <your project> --no-move --apply
```

`--no-move` keeps your project's `plugins/` directory where it is. The full
procedure is in the documentation's *Migrating to 1.0* page.
