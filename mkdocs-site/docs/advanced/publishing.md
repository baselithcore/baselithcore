# Quick Guide: Publishing on PyPI

`baselith-core` is published to PyPI by the CI pipeline, not by hand. This
guide explains what the pipeline does and how to dry-run a build locally.

## 1. Versioning is automatic

The version lives in `core/_version.py` (single source of truth):

```python
__version__ = "0.31.0"
```

Do **not** edit it manually. semantic-release rewrites the file
(`@semantic-release/exec` `prepareCmd` in `.releaserc`) from the Conventional
Commits merged to `main` — `fix:`/`perf:` bump PATCH, `feat:` bumps MINOR, a
`BREAKING CHANGE:` footer bumps MAJOR. See
[Versioning & Deprecation](versioning-and-deprecation.md).

## 2. The release pipeline

Both jobs live in `.github/workflows/ci.yml` and run only on `main`:

1. **`release` (Semantic Release)** — after `python_test` passes, semantic-release
   analyses the commits, writes `core/_version.py` and `CHANGELOG.md`, commits
   them as `chore(release): <version> [skip ci]`, tags `v<version>` and creates
   the GitHub Release. If a release was cut it then builds the distribution
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
