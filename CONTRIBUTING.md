# Contributing to BaselithCore

Thank you for your interest in contributing! This document provides the guidelines for participating in the development of the framework.

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How to Contribute](#how-to-contribute)
- [Development Environment](#development-environment)
- [Code Standards](#code-standards)
- [Pull Requests](#pull-requests)
- [Bug Reporting](#bug-reporting)

---

## Code of Conduct

This project adopts a respectful and collaborative code of conduct. We expect all contributors to:

- Be respectful and inclusive
- Accept constructive feedback
- Focus on improving the project
- Help new contributors

---

## How to Contribute

### Types of Contributions

1. **Bug Fixes**: Correcting existing problems
2. **Features**: New functionality (discuss via Issue first)
3. **Documentation**: Improvements to the documentation
4. **Testing**: Increasing test coverage
5. **Plugins**: New plugins in the `plugins/` directory

### Workflow

1. **Fork** the repository
2. **Create a branch** from `main`: `git checkout -b feature/feature-name`
3. **Implement** changes following the project standards
4. **Test** your changes: `python -m pytest`
5. **Commit** using [Conventional Commits](#commit-format) — **mandatory**, not a
   style preference: `semantic-release` derives the version number, the
   changelog entry and whether a release happens at all from the commit type
6. **Push** and open a **Pull Request**

---

## Development Environment

### Fastest path: Dev Container

Open the repo in a [Dev Container](https://containers.dev) (VS Code "Reopen in
Container" or GitHub Codespaces) and the whole environment — Python 3.12 + dev
tooling + pre-commit + Node + Docker access — builds itself. See
[`.devcontainer/README.md`](.devcontainer/README.md). Otherwise, set up manually
below.

### Initial Setup

```bash
# Clone the repository
git clone <repository-url>
cd baselith-core

# Recommended: uv creates the virtualenv, reads .python-version (3.12) and
# installs the LOCKED set from uv.lock — the same one CI tests against.
# `dev` is the default group, so this includes the test suite and the toolchain.
uv sync

# Equivalent with pip (>= 25.1, for the --group flag)
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -e . --group dev

# Optional capability groups are EXTRAS, not dependency groups: they are part
# of the published contract, so they are installed by extra either way.
# uv sync --extra rag --extra browser --extra web
# pip install -e ".[documents,ocr,nlp]"

# Install pre-commit hooks
pre-commit install
```

> **Extras vs dependency groups.** `[project.optional-dependencies]` holds the
> runtime capability groups (`rag`, `browser`, `documents`, `qdrant`, ...) —
> those ship in the wheel's metadata and any consumer can ask for them.
> `[dependency-groups]` (PEP 735) holds `test`, `dev` and `docs` — development
> inputs that are resolved from this repository and never travel with the
> distribution, which is why this project's exact `ruff==`/`mypy==` CI pins are
> no longer advertised as installable requirements of the library. Build the
> documentation site with `uv sync --no-default-groups --group docs`, then
> `zensical build --clean` from `mkdocs-site/`.

### Setup Verification

```bash
# Run diagnostics
baselith doctor

# Run tests
python -m pytest

# Run linting
ruff check .
mypy core/
```

### Dependency policy

Three files express dependencies, each with a distinct job — keep them consistent
when adding or changing a dependency:

- **`pyproject.toml`** — the **library** contract. Use **compatible ranges**
  (`>=floor,<next-major`), never exact `==` pins: exact pins force resolution
  conflicts on downstream consumers and block their security patches.
- **`uv.lock`** — the reproducibility lock for development and CI (`uv sync`).
  This, not exact pins, is what guarantees a repeatable dev/test environment.
  It covers the extras **and** the PEP 735 dependency groups; CI installs from
  it with `uv export --frozen --no-default-groups --group test`, never by
  re-resolving. `uv lock --check` is its own CI job.
- **`requirements.txt`** — the human-readable mirror of the dependency surface
  the container images install. Keep every spec **identical** to the matching
  entry in `pyproject.toml`; `scripts/check_requirements_sync.py` (CI job
  `requirements_sync`) fails the build otherwise. It is **not** what the image
  installs from: the `Dockerfile` materialises `uv.lock` with
  `uv export --frozen`, so the image gets the same fully-pinned transitive set
  CI tested, which a range-based requirements file cannot give it.

### Local Services (Docker)

To run supporting services:

```bash
docker compose up -d
```

---

## Code Standards

### Fundamental Rule

> **The Core is Sacred**: The `core/` directory contains ONLY domain-agnostic logic.
> Any domain-specific logic MUST be implemented as a plugin in `plugins/`.

### Python Standards

- **Python 3.12+** with rigorous type hints
- **Pydantic** for configurations and models
- **Async/Await** for all I/O operations
- **Google-style Docstrings** for public classes and functions

### Linting & Formatting

The project uses:

- **Ruff**: Linting and formatting
- **Mypy**: Static type checking
- **Pre-commit**: Automatic hooks

```bash
# Verify before committing
pre-commit run --all-files
```

### Testing

- **Pytest** for unit and integration tests
- Minimum enforced **branch**-coverage gate: **78%** (`--cov-fail-under` in
  [`pytest.ini`](pytest.ini)). It is a ratchet — raise it as coverage grows,
  never lower it to make a branch pass.
- Strict `mypy` gate on hardened core resilience modules
- Mock external dependencies (LLM, DB)

```bash
# Run all tests
python -m pytest

# Run with coverage
python -m pytest --cov=core --cov-report=html

# Run specific tests
python -m pytest tests/unit/core/reasoning/ -v
```

### File Structure

- Python Files: max **500 lines** (modularize if necessary)
- Every module must have an `__init__.py` with explicit exports
- Plugins: follow the standard structure (see `plugins/example-plugin/`)

---

## Pull Requests

### Checklist

Before opening a PR, ensure that:

- [ ] All tests pass: `python -m pytest`
- [ ] No linting errors: `ruff check .`
- [ ] Type checking OK: `mypy core/`
- [ ] Focused strict typing gates pass: `python scripts/check_official_plugin_typing.py` and `python scripts/check_core_strict_typing.py`
- [ ] Pre-commit passes: `pre-commit run --all-files`
- [ ] Documentation updated (if necessary)
- [ ] Commit messages follow [Conventional Commits](#commit-format) (mandatory)

### Commit Format

**[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) are
mandatory.** This is not a house style — it is the input to an automated
pipeline. `@semantic-release/commit-analyzer` (configured in
[`.releaserc`](.releaserc)) reads the type of every commit on `main` to decide
whether to cut a release, whether it is a major/minor/patch, and which section
of [`CHANGELOG.md`](CHANGELOG.md) the entry lands in. A commit that does not
parse is silently treated as no-release: the fix ships in the tree and never
reaches PyPI, the container image or the Helm chart.

```text
type(scope): short description

Optional body explaining *why*, wrapped at 72 columns.

BREAKING CHANGE: what an operator must do differently.
```

The subject is imperative and lower-case, with no trailing period. The scope is
optional and names the subsystem (`orchestration`, `memory`, `plugins`,
`docker`, `chart`, ...).

**Allowed types**, and what each one releases:

| Type       | Release   | Use for                                              |
| ---------- | --------- | ---------------------------------------------------- |
| `feat`     | **minor** | New user-visible capability                           |
| `fix`      | **patch** | Bug fix                                               |
| `perf`     | **patch** | Performance improvement with no behaviour change      |
| `refactor` | none      | Internal restructuring, identical behaviour           |
| `docs`     | none      | Documentation only                                    |
| `test`     | none      | Tests only                                            |
| `build`    | none      | Packaging, `pyproject.toml`, Dockerfile, dependencies |
| `ci`       | none      | Workflows and repository gates                        |
| `style`    | none      | Formatting only, no code change                       |
| `chore`    | none      | Anything else that ships nothing                      |
| `revert`   | **patch** | Reverting a previous commit                           |

A `BREAKING CHANGE:` footer (or a `!` after the type, e.g. `feat(api)!:`) forces
a **major** release regardless of type. Pre-1.0 that is still a real signal —
use it only for a change an operator has to act on.

**Examples**:

- `feat(reasoning): add MCTS strategy to TreeOfThoughts`
- `fix(cache): handle Redis connection timeout`
- `perf(memory): batch MTM consolidation writes`
- `build(docker): install from the uv lock export instead of requirements.txt`
- `docs(readme): update installation instructions`

---

## Bug Reporting

### How to Report

Open an Issue including:

1. Clear **Description** of the bug
2. **Steps to reproduce** the problem
3. **Expected behavior** vs observed behavior
4. **Environment**: Python version, OS, relevant dependencies
5. **Logs** or traceback (if available)

### Issue Template

```markdown
## Description
[Describe the bug]

## Steps to Reproduce
1. ...
2. ...

## Expected Behavior
[What you expected]

## Observed Behavior
[What actually happened]

## Environment
- Python: 3.x.x
- OS: macOS/Linux/Windows
- Commit: [hash]
```

---

## Resources

- [Architecture](mkdocs-site/docs/architecture/overview.md)
- [Plugin System](mkdocs-site/docs/plugins/architecture.md)
- [Development Guide](mkdocs-site/docs/advanced/deployment.md)
- [Quick Start](mkdocs-site/docs/getting-started/quickstart.md)

---

## License

This project is released under the **AGPL v3** license.
By contributing to this project, you agree that your contributions will be released under the same license.

---

Thank you for your contribution! 🚀
