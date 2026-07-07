---
title: Plugin Packaging
description: Package plugins for distribution
---

**Plugin Packaging** is the process of preparing a plugin for distribution. A well-structured package ensures reliable installation, safe updates, and compatibility with different framework versions.

!!! info "Why Package Plugins?"
    - **Distribution**: Share your plugin with other users
    - **Versioning**: Manage multiple versions systematically
    - **Dependencies**: Declare and automatically manage dependencies
    - **Validation**: Automatic structure and security verification

---

## Package Structure

A plugin package requires a well-defined structure:

```text
my-plugin-1.0.0/
├── plugin.py            # Entry point (REQUIRED)
├── manifest.yaml        # Package metadata (REQUIRED)
├── README.md            # Documentation (REQUIRED)
├── CHANGELOG.md         # Version history (recommended)
├── agent.py             # Agent implementation (if agent plugin)
├── handlers.py          # Flow handlers (if applicable)
├── static/              # Frontend assets (if UI plugin)
│   ├── components.js
│   └── styles.css
└── tests/               # Test suite (recommended)
    ├── __init__.py
    ├── test_plugin.py
    └── test_handlers.py
```

### Required Files

| File            | Purpose                                | Validation                                  |
| --------------- | -------------------------------------- | ------------------------------------------- |
| `plugin.py`     | Entry point, Plugin class              | Must contain class inheriting from `Plugin` |
| `manifest.yaml` | Version metadata, author, dependencies | Valid manifest schema                       |
| `README.md`     | User documentation                     | Not empty                                   |

### Recommended Files

| File               | Purpose             | Benefit                         |
| ------------------ | ------------------- | ------------------------------- |
| `CHANGELOG.md`     | Change history      | Users understand what changed   |
| `tests/`           | Test suite          | Increases confidence and rating |
| `python_dependencies` in `manifest.yaml` | Runtime dependencies | Declarative plugin installation |

---

## Manifest

The `manifest.yaml` file contains all plugin metadata:

```yaml title="manifest.yaml"
name: my-plugin
version: 1.0.0
description: Brief but informative plugin description
author: Your Name
homepage: https://github.com/you/my-plugin
license: MIT
tags:
  - utility
  - helper
category: utility
python_dependencies:
  - httpx>=0.25,<1.0
  - pydantic>=2.0
plugin_dependencies:
  core-utilities: ^1.0
required_resources:
  - llm
optional_resources:
  - postgres
environment_variables:
  - MY_PLUGIN_API_KEY
integrity_sha256: 7c2a1b...e9f0   # Optional. SHA-256 of the plugin's *.py/*.pyi + build files (manifest and ui/ excluded).
```

### Manifest Fields

| Field                   | Required | Description                                      |
| ----------------------- | -------- | ------------------------------------------------ |
| `name`                  | ✅        | Unique plugin name (lowercase, hyphen-separated) |
| `version`               | ✅        | SemVer version (e.g., "1.0.0")                   |
| `description`           | ✅        | Brief description (max 200 characters)           |
| `author`                | ✅        | Author name or organization                      |
| `license`               | ✅        | License (MIT, Apache-2.0, GPL-3.0, etc.)         |
| `min_core_version`      | ❌        | Minimum BaselithCore version (PEP 440)           |
| `python_dependencies`   | ❌        | Pip-style package requirements                   |
| `plugin_dependencies`   | ❌        | Required plugins with version constraints        |
| `required_resources`    | ❌        | Core resources needed by the plugin              |
| `optional_resources`    | ❌        | Optional resources used when available           |
| `environment_variables` | ❌        | Required environment variables                   |
| `integrity_sha256`      | ❌        | Hex SHA-256 of the plugin's `*.py`/`*.pyi` files plus the build/packaging files `pip install` trusts (`pyproject.toml`, `setup.cfg`, `MANIFEST.in`, `requirements*.txt`). The manifest itself and the `ui/`, `__pycache__`, `.git`, and `node_modules` directories are **excluded** from the digest, so the publisher can inject this field into the manifest after computing the hash without invalidating it. Verified before `exec_module`; mismatch refuses load. In production a plugin without this field is refused by default (fail-closed) unless `BASELITH_ALLOW_UNSIGNED_IN_PROD=true`; set `BASELITH_REQUIRE_SIGNED_PLUGINS=true` to reject unsigned plugins in every environment. Compute via `baselith plugin sign` or `core.plugins.integrity.compute_plugin_hash()`. |

### Dependencies

Specify dependencies with version ranges in `manifest.yaml`:

```yaml
python_dependencies:
  - httpx>=0.25,<1.0
  - pydantic>=2.0
  - numpy~=1.24.0
plugin_dependencies:
  base-plugin: ^1.0
  helper-plugin: ~1.2.3
```

---

## Signing for Integrity {#integrity}

The framework verifies a plugin's `integrity_sha256` digest before importing any of its
code. Use `baselith plugin sign` to compute the digest over the plugin's executable
surface and write it into the manifest:

```bash
# Compute the digest and write it into manifest.(yaml|yml|json)
baselith plugin sign plugins/my-plugin

# Compute and print the digest without modifying the manifest
baselith plugin sign plugins/my-plugin --check
```

| Argument / Flag | Description                                                        |
| --------------- | ------------------------------------------------------------------ |
| `path`          | Path to the local plugin directory                                 |
| `--check`       | Print the computed hash without modifying the manifest             |

!!! info "What is hashed"
    The digest covers `*.py`/`*.pyi` source files **plus** the build and packaging
    files that `pip install` executes or trusts (`pyproject.toml`, `setup.cfg`,
    `MANIFEST.in`, `requirements*.txt`), sorted by POSIX-relative path.
    The manifest itself and the `ui/`, `__pycache__`, `.git`, and `node_modules`
    directories are excluded. This is why `sign` can write the hash back into the manifest
    without invalidating it.
    Plugins signed before 0.17 (source-only digest) keep loading with a warning;
    re-sign them to extend coverage. Under `BASELITH_REQUIRE_SIGNED_PLUGINS=true`
    the legacy digest is **refused** — strict mode demands the full surface.

!!! warning "Enforcing signatures"
    In **production** the loader is fail-closed by default: a plugin lacking a valid
    `integrity_sha256` is refused unless `BASELITH_ALLOW_UNSIGNED_IN_PROD=true` is set
    (insecure opt-out). Set `BASELITH_REQUIRE_SIGNED_PLUGINS=true` to enforce signing in
    **every** environment. A mismatch between the computed and declared hash always
    refuses the load.

!!! note "Distribution archives"
    The framework ships no `plugin package` command. To distribute a plugin, publish it to
    the marketplace with `baselith plugin marketplace publish <path>` (which packages and
    uploads it for you), or distribute the plugin directory / a standard archive yourself.

---

## Validation

Before packaging, you can validate the plugin separately:

```bash
baselith plugin validate plugins/my-plugin/
```

### Validation Checks

1. **Structure**: Required files present
2. **Manifest**: Valid JSON, required fields
3. **Python Syntax**: Code syntactically correct
4. **Imports**: No dangerous imports
5. **Security**: No potentially harmful patterns
6. **Dependencies**: Compatible with framework

### Fixing Common Errors

**Error: "Invalid manifest.yaml"**

```bash
# Validate YAML
python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('plugins/my-plugin/manifest.yaml').read_text())"
```

**Error: "Missing required field: version"**

```json
// Add missing field
{
  "name": "my-plugin",
  "version": "1.0.0",  // <- Add this
  ...
}
```

**Error: "Dangerous import: subprocess"**

```python
# ❌ Don't use
import subprocess
result = subprocess.run(cmd)

# ✅ Use safe alternatives
from core.utils import safe_shell_command
result = await safe...shell_command(cmd, allowed_commands=["ls", "cat"])
```

---

## CI/CD Integration

Automate packaging and publishing with CI/CD.

### Authenticating a non-interactive pipeline

CI jobs cannot use the interactive login prompt, so supply the credential as a CI
secret. Choose the path that matches who you are.

**Hub operators** set `MARKETPLACE_API_KEY` (the server key) as shown in the
examples below; `publish` picks it up automatically.

**External publishers** store a **GitHub token** (a classic PAT with no scopes)
as a secret and exchange it for a session at the start of the job:

```bash
baselith plugin marketplace login --github-token "$GITHUB_MARKETPLACE_TOKEN"
baselith plugin marketplace publish .
```

Each run mints a fresh ~7-day session JWT — long-lived enough for the job, while
the PAT's own lifetime stays under your control. See
[Marketplace › Authentication](marketplace.md#publishing).

!!! tip "Prefer Backstage for release orchestration"
    The [Backstage Publish template](backstage-publish.md) offers a
    zero-config alternative: the framework's
    `POST /api/backstage/publish` endpoint wraps the zipping + submission
    step for you, and the optional GitHub mirror ships a ready-made
    `marketplace-publish.yml` workflow identical in spirit to the one
    below. Keep the raw GitHub Actions recipe if you need a fully
    air-gapped, Backstage-less release path.

### GitHub Actions

```yaml title=".github/workflows/publish.yml"
name: Publish Plugin

on:
  push:
    tags:
      - 'v*'

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Validate plugin
        run: baselith plugin validate .

      - name: Run tests
        run: pytest tests/

      - name: Sign plugin (write integrity_sha256)
        run: baselith plugin sign .

      - name: Publish to marketplace
        env:
          MARKETPLACE_API_KEY: ${{ secrets.MARKETPLACE_API_KEY }}
        run: |
          baselith plugin marketplace publish .
```

### GitLab CI

```yaml title=".gitlab-ci.yml"
stages:
  - validate
  - test
  - sign
  - publish

validate:
  stage: validate
  script:
    - baselith plugin validate .

test:
  stage: test
  script:
    - pytest tests/ --cov=.

sign:
  stage: sign
  script:
    - baselith plugin sign .
  artifacts:
    paths:
      - manifest.yaml

publish:
  stage: publish
  only:
    - tags
  script:
    - baselith plugin marketplace publish .
```

---

## Pre-Publication Checklist

Before publishing, verify:

### Code

- [ ] All tests pass
- [ ] No critical TODO or FIXME
- [ ] Code formatted (black, ruff)
- [ ] Type hints present

### Documentation

- [ ] README.md updated
- [ ] CHANGELOG.md with new changes
- [ ] Docstrings on public classes and functions
- [ ] Usage examples included

### Metadata

- [ ] `manifest.json` valid
- [ ] Version incremented (SemVer)
- [ ] Dependencies updated
- [ ] `min_framework_version` correct

### Security

- [ ] No hardcoded secrets
- [ ] No dangerous imports
- [ ] Input validation on all endpoints

---

## Troubleshooting

### "Package too large"

**Problem**: Package exceeds size limit.

**Solution**: Exclude unnecessary files:

```json title="manifest.json"
{
  "exclude": [
    "tests/",
    "docs/",
    "*.pyc",
    "__pycache__/",
    ".git/"
  ]
}
```

### "Dependency conflict"

**Problem**: Two dependencies require incompatible versions.

**Solution**: Use more flexible version ranges:

```json
{
  "dependencies": {
    "python": [
      "packageA>=1.0,<3.0",    // Wider range
      "packageB>=2.0"
    ]
  }
}
```

### "Validation failed: missing entry point"

**Problem**: `plugin.py` doesn't contain a valid Plugin class.

**Solution**: Ensure `plugin.py` contains:

```python
from core.plugins import Plugin

class MyPlugin(Plugin):
    """Main plugin class."""

    name = "my-plugin"
    version = "1.0.0"
```

---

## Best Practices

!!! tip "Versioning"
    Use Semantic Versioning (MAJOR.MINOR.PATCH). Never modify an already published version.

!!! tip "Dependencies"
    Specify minimum versions with flexible ranges (`>=1.0`), avoid exact pins (`==1.0.0`) when possible.

!!! warning "Testing"
    Always test the package in a clean environment before publishing. Use virtualenv or Docker.

!!! tip "Changelog"
    Maintain a CHANGELOG.md following the [Keep a Changelog](https://keepachangelog.com/) format.

!!! tip "License"
    Always include a license. MIT is recommended for open source plugins.
