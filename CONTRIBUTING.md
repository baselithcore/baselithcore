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
5. **Commit** with descriptive messages
6. **Push** and open a **Pull Request**

---

## Development Environment

### Initial Setup

```bash
# Clone the repository
git clone <repository-url>
cd baselith-core

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Install pre-commit hooks
pip install pre-commit
pre-commit install
```

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

### Local Services (Docker)

To run supporting services:

```bash
docker-compose up -d
```

---

## Code Standards

### Fundamental Rule

> **The Core is Sacred**: The `core/` directory contains ONLY domain-agnostic logic.
> Any domain-specific logic MUST be implemented as a plugin in `plugins/`.

### Python Standards

- **Python 3.10+** with rigorous type hints
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
- Minimum target coverage: **66%**
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
- [ ] Pre-commit passes: `pre-commit run --all-files`
- [ ] Documentation updated (if necessary)
- [ ] Clear and descriptive commit messages

### Commit Format

Use descriptive commit messages:

```text
type(scope): short description

Detailed description of changes (optional).
```

**Types**: `feat`, `fix`, `docs`, `test`, `refactor`, `style`, `chore`

**Examples**:

- `feat(reasoning): add MCTS strategy to TreeOfThoughts`
- `fix(cache): handle Redis connection timeout`
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
