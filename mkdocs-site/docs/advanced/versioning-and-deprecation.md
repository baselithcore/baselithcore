# Versioning & Deprecation Policy

BaselithCore follows [Semantic Versioning](https://semver.org) (`MAJOR.MINOR.PATCH`)
and ships changes through Conventional Commits + semantic-release.

## Version bumps

| Commit type | Release | Meaning |
|---|---|---|
| `fix:` / `perf:` | PATCH | Backward-compatible bug/perf fix |
| `feat:` | MINOR | Backward-compatible new capability |
| `feat!:` / `BREAKING CHANGE:` | MAJOR | Backward-incompatible change |
| `docs:` `chore:` `refactor:` `test:` `ci:` | none | No release |

The version lives in `core/_version.py` (single source of truth) and is written
by the release pipeline. The [CHANGELOG](https://keepachangelog.com) is generated
and committed automatically.

## What counts as a breaking change

- Removing or renaming a public symbol exported from a `core.*` package
  `__init__`, or changing its signature incompatibly.
- Removing/renaming an HTTP route, or a backward-incompatible change to a
  response schema or status code.
- Removing a configuration/env var, or changing its default in a way that alters
  behaviour.
- Tightening the plugin manifest contract or the plugin ABI.

Additive changes (new optional params with safe defaults, new routes, new env
vars, new flags) are **not** breaking.

## Deprecation process

Breaking changes are staged, never abrupt:

1. **Announce** — mark the old surface deprecated in the same release that ships
   the replacement. Add a `Deprecated` entry to the CHANGELOG and emit a
   `DeprecationWarning` at runtime (and a log line for HTTP/CLI surfaces).
2. **Overlap** — keep the deprecated surface working for **at least one MINOR
   release** (≥ 90 days for public APIs), with old and new available together.
3. **Remove** — delete only in the next **MAJOR** release, referencing the
   deprecation in the CHANGELOG.

```python
import warnings

def old_api(*args, **kwargs):
    warnings.warn(
        "old_api() is deprecated since 0.12 and will be removed in 1.0; "
        "use new_api() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return new_api(*args, **kwargs)
```

## HTTP API versioning

Data routes are served both unprefixed and under `/v1` (see
[REST API](../api/rest.md#api-versioning)). When a breaking API change is
needed, introduce `/v2` alongside `/v1` and deprecate `/v1` per the process
above — clients pinned to `/v1` keep working through the overlap window.

## Configuration & env vars

When renaming a setting, accept both names for the overlap window (alias the old
to the new) and emit a deprecation warning when the old one is used. Document the
change in the CHANGELOG under `Deprecated`.

## Plugins

The plugin manifest declares core-version bounds; the loader enforces them. A
breaking change to the plugin ABI is a MAJOR bump and must be called out so
plugin authors can update their bounds.
