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
integrity_sha256: 7c2a1b...e9f0   # Optional. SHA-256 of the plugin's executable surface.
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
| `integrity_sha256`      | ❌        | Hex SHA-256 of the plugin's `*.py`/`*.pyi` files plus the manifest. Verified before `exec_module`; mismatch refuses load. Set `BASELITH_REQUIRE_SIGNED_PLUGINS=true` to reject any plugin without this field. Compute via `core.plugins.integrity.compute_plugin_hash()`. |

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

## Package Command

Use the CLI to create the distributable package:

```bash
baselith plugin package my-plugin --output dist/
```

### Process Output

```text
┌─────────────────────────────────────────────────────┐
│  Plugin Packaging: my-plugin                        │
└─────────────────────────────────────────────────────┘

Step 1/5: Validating structure...
  ✅ plugin.py found
  ✅ manifest.json valid
  ✅ README.md present
  ✅ All required files present

Step 2/5: Checking dependencies...
  ✅ httpx>=0.25 compatible
  ✅ pydantic>=2.0 compatible
  ✅ No conflicting dependencies

Step 3/5: Running security scan...
  ✅ No dangerous imports
  ✅ No system calls
  ✅ No hardcoded secrets

Step 4/5: Running tests...
  ✅ tests/test_plugin.py: 5/5 passed
  ✅ tests/test_handlers.py: 3/3 passed
  ✅ All tests passed

Step 5/5: Creating archive...
  ✅ Package created: dist/my-plugin-1.0.0.tar.gz
  ✅ Size: 45.2 KB
  ✅ SHA256: abc123...

✅ Package ready for distribution!
```

### Command Options

| Option                   | Description                           |
| ------------------------ | ------------------------------------- |
| `--output DIR`           | Output directory (default: `dist/`)   |
| `--format [tar.gz\|zip]` | Archive format                        |
| `--skip-tests`           | Skip test execution (not recommended) |
| `--skip-validation`      | Skip validation (development only)    |
| `--include-dev`          | Include dev dependencies              |

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
      
      - name: Package plugin
        run: baselith plugin package . --output dist/
      
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
  - package
  - publish

validate:
  stage: validate
  script:
    - baselith plugin validate .

test:
  stage: test
  script:
    - pytest tests/ --cov=.

package:
  stage: package
  script:
    - baselith plugin package . --output dist/
  artifacts:
    paths:
      - dist/

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
