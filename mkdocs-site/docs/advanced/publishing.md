# Quick Guide: Publishing on PyPI

`baselith-core` is published to PyPI by the CI pipeline, not by hand. This
guide explains what the pipeline does and how to dry-run a build locally.

## 1. Versioning is automatic

The version lives in `core/_version.py` (single source of truth):

```python
__version__ = "0.31.0"
```

The number itself is derived, never chosen: semantic-release reads the
Conventional Commits merged to `main` — `fix:`/`perf:` bump PATCH, `feat:` bumps
MINOR, a `BREAKING CHANGE:` footer bumps MAJOR. See
[Versioning & Deprecation](versioning-and-deprecation.md).

`main` is pull-request-only (a repository ruleset), and a workflow's
`GITHUB_TOKEN` can never bypass a ruleset, so the release job **cannot commit
the bump back**. Run `.releaserc`'s `prepareCmd` in the release-prep pull
request instead — it rewrites every file that carries the version in one go —
and add the CHANGELOG entry there. The release job still runs the same
`prepareCmd` on the runner, so the published artifacts carry the right version
even if that step was forgotten; only the tracked files would be left behind.

## 2. The release pipeline

Both jobs live in `.github/workflows/ci.yml` and run only on `main`:

1. **`release` (Semantic Release)** — after `python_test` passes, semantic-release
   analyses the commits, writes `core/_version.py` on the runner — plus every
   other file that carries the version: the Helm chart's `appVersion`, the
   `SECURITY.md` support table, both client SDKs and `info.version` in the two
   checked-in `openapi.json` copies — then tags `v<version>` and creates the
   GitHub Release. Those rewrites stay on the runner and feed the build; the
   commit that carries them into the tree is the release-prep pull request's,
   because `main` takes no direct pushes. If a release was cut it then builds
   the distribution
   with `python3 -m build`, generates a CycloneDX SBOM (attached to the GitHub
   Release), attests build provenance for `dist/*`
   (`actions/attest-build-provenance`) and uploads `dist/` as the
   `python-package-dist` artifact.
2. **`publish_pypi` (Publish to PyPI)** — runs only when `release` reports
   `new_release_published == 'true'`. It downloads `python-package-dist` and
   publishes it with `pypa/gh-action-pypi-publish` using **trusted publishing**
   (OIDC via the `pypi` environment; no API token) with PEP 740 attestations
   enabled.

There is nothing to type: merge a releasable commit to `main` and the wheel
appears on PyPI once both jobs are green.

## 3. Local dry-run (build only)

To check that the package still builds and its metadata is valid, without
publishing:

```bash
pip install build twine
python -m build
twine check dist/*
```

This creates `dist/` with the sdist and wheel. Do not `twine upload` from a
workstation — the PyPI project is configured for trusted publishing from CI,
which is where the provenance attestations come from.

!!! tip "Testing a change to the packaging"
    Inspect the wheel contents with `unzip -l dist/*.whl` — for example to
    confirm `plugins/baselithbot/ui/dist/**` is included and `ui/src/` is not.
