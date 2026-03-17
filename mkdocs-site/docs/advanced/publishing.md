# Quick Guide: Publishing on PyPI

This guide explains how to publish `baselith-core` on PyPI in a few steps.

## 1. Requirements

Ensure you have the necessary tools installed:

```bash
pip install build twine
```

## 2. Verify Version

Check the version in `pyproject.toml`:

```toml
[project]
version = "0.3.0"
```

## 3. Package Build

Generate the files for distribution:

```bash
python -m build
```

This will create the `dist/` folder.

## 4. Upload to PyPI

Use `twine` to upload the package:

```bash
twine upload dist/*
```

**Credentials:**

- **Username:** `__token__`
- **Password:** Paste your PyPI API Token (`pypi-...`)

> [!TIP]
> To test without actually publishing, use TestPyPI:
> `twine upload --repository testpypi dist/*`
