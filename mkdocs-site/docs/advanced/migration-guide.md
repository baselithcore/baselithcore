# v0.3.x → v0.4.0 Migration Guide

## Overview

BaselithCore v0.4.0 completes the architectural refactoring to enforce the **Sacred Core** principle. This guide helps users migrate plugins and applications from v0.3.x to v0.4.0.

---

## Breaking Changes

### Moved Modules

The following modules have been moved from `core/` to `plugins/` to enforce architectural compliance:

| v0.3.x Location             | v0.4.0 Location            | Migration Impact |
| --------------------------- | -------------------------- | ---------------- |
| `core.agents.browser_agent` | `plugins.browser_agent`    | Update imports   |
| `core.agents.coding`        | `plugins.coding_agent`     | Update imports   |
| `core.doc_sources`          | `plugins.document_sources` | Update imports   |
| `core.scraper`              | `plugins.web_scraper`      | Update imports   |
| `core.routers.*`            | `plugins.api_routers`      | Update imports   |

Every old path is kept as a **deprecated compatibility shim** that re-exports the
plugin module (for example `core/routers/tenant.py` registers itself as
`plugins.api_routers.tenant` in `sys.modules`), so v0.3.x imports still resolve
and point at the same objects. They are deprecated, not removed. `core.chat` and
`core.personas` (including `core.personas.defaults.HELPFUL_ASSISTANT`) stay in
`core/` and need no change.

---

## Import Updates Required

### Before (v0.3.x)

```python
from core.agents.browser_agent import BrowserAgent
from core.agents.coding.agent import CodingAgent
from core.doc_sources.web import WebDocumentSource
from core.doc_sources.readers import read_pdf
from core.scraper import Scraper
from core.routers.chat import router as chat_router
```

### After (v0.4.0)

```python
from plugins.browser_agent import BrowserAgent
from plugins.coding_agent import CodingAgent
from plugins.document_sources.web import WebDocumentSource
from plugins.document_sources.readers import read_pdf
from plugins.web_scraper import Scraper
from plugins.api_routers.chat import router as chat_router
```

---

## Configuration Changes

### Plugin Configuration

Functionality that used to be always-on in `core/` is now provided by plugins,
so enable the ones you use in `configs/plugins.yaml`. The file is a **mapping
keyed by plugin name** (the plugin's directory name); each entry carries
`enabled` plus the plugin's own settings at the same level (the shipped file
sets `reasoning_agent.max_steps`, for example):

```yaml
browser_agent:
  enabled: true
coding_agent:
  enabled: true
document_sources:
  enabled: true
web_scraper:
  enabled: true
```

When the file is non-empty, a plugin that is absent from it (or has
`enabled: false`) is not discovered at all — see
[Lazy Loading](lazy-loading.md#plugin-activation-at-startup).

---

## OCR Backend Migration (Chandra → MinerU)

The `chandra` OCR backend has been replaced by [MinerU](https://github.com/opendatalab/MinerU) as the primary OCR engine (Tesseract remains the lightweight fallback):

- `PDF_OCR_BACKEND` now accepts `auto | mineru | tesseract` (default `mineru`). A legacy `chandra` value is automatically mapped to `mineru` with a logged warning — startup does not break.
- All `CHANDRA_*` environment variables are ignored; configure MinerU via `MINERU_BACKEND`, `MINERU_LANG`, `MINERU_FORMULA_ENABLE`, `MINERU_TABLE_ENABLE`, `MINERU_SERVER_URL`, `MINERU_MODEL_SOURCE`.
- Install with `pip install "baselith-core[mineru]"` (heavy: torch stack). Models download on first use — pre-fetch with `mineru-models-download`; if MinerU is not installed, OCR gracefully falls back to Tesseract.
- Output change: MinerU produces whole-document Markdown **without** per-page `[Pagina N]` markers (the Tesseract fallback still emits them). Re-indexed OCR documents will produce new fingerprints.

---

## API Changes

### Router Endpoints

**v0.3.x** - Routers were in core:

```python
from fastapi import FastAPI
from core.routers import chat, feedback, admin

app = FastAPI()
app.include_router(chat.router)
app.include_router(feedback.router)
app.include_router(admin.router)
```

**v0.4.0** - Routers ship with the `api_routers` plugin (`create_app()` mounts
them for you; `core.routers.*` stays importable as a deprecated re-export):

```python
from fastapi import FastAPI
from plugins.api_routers import chat, feedback, admin

app = FastAPI()
app.include_router(chat.router)
app.include_router(feedback.router)
app.include_router(admin.router)
```

---

## Deprecation Timeline

| Version     | Date    | Status                                                              |
| ----------- | ------- | ------------------------------------------------------------------- |
| **v0.3.0**  | 2026-03 | Last release with agents, doc sources, scraper and routers in `core/` |
| **v0.4.0**  | 2026-03 | Sacred Core layout: modules moved to `plugins/`, `core.*` paths kept as shims |
| **v0.30.0** | 2026-09 | The compatibility shims are still importable                        |
| **v0.31.0** | 2026-09 | Current release; durable tool ledger, durable event stream, plugin permission enforcement |

---

## Migration Steps

There is no automated migration command in the `baselith` CLI; the change is a
mechanical import rewrite:

1. **Find deprecated imports**:

   ```bash
   grep -r "from core.agents" your_project/
   grep -r "from core.doc_sources" your_project/
   grep -r "from core.scraper" your_project/
   grep -r "from core.routers" your_project/
   ```

2. **Update imports** using find-replace:
   - `from core.agents` → `from plugins.browser_agent` or `plugins.coding_agent`
   - `from core.doc_sources` → `from plugins.document_sources`
   - `from core.scraper` → `from plugins.web_scraper`
   - `from core.routers` → `from plugins.api_routers`

3. **Update plugin config**:
   - Add required plugins to `configs/plugins.yaml`

4. **Test your application**:

   ```bash
   pytest tests/
   python -m your_app
   ```

---

## Plugin Compatibility

### v0.3.x Plugins

All v0.3.x plugins will continue to work in v0.4.0 with minimal changes:

- ✅ **Custom plugins**: No changes needed
- ✅ **Plugin interfaces**: Unchanged
- **Imports from moved modules**: Update imports

### Example: Updating a Custom Plugin

**Before (v0.3.x)**:

```python
# my_plugin/handler.py
from core.agents.browser_agent import BrowserAgent

class MyHandler(FlowHandlerMixin):
    def __init__(self):
        self.browser = BrowserAgent()
```

**After (v0.4.0)**:

```python
# my_plugin/handler.py
from plugins.browser_agent import BrowserAgent

class MyHandler(FlowHandlerMixin):
    def __init__(self):
        self.browser = BrowserAgent()
```

---

## Testing Your Migration

### Pre-Migration Checklist

- [ ] Backup your codebase: `git commit -am "Pre-migration backup"`
- [ ] Review current imports: `grep -rn "from core\.\(agents\|doc_sources\|scraper\|routers\)" your_project/`
- [ ] Note all dependencies on moved modules

### Post-Migration Verification

1. **Run tests**:

   ```bash
   pytest tests/ -v
   ```

2. **Check imports**:

   ```bash
   python -c "from plugins.browser_agent import BrowserAgent; print('OK')"
   ```

3. **Verify plugin loading**:

   ```bash
   baselith plugin list
   ```

4. **Start your application**:

   ```bash
   python -m your_app
   # Or: uvicorn your_app:app
   ```

---

## Common Migration Issues

### Issue 1: Old imports still work

**Symptom**: `from core.agents import BrowserAgent` keeps resolving after the
upgrade, so nothing forces the rewrite.

**Explanation**: that is the compatibility shim — `core.agents.browser_agent`
imports `BrowserAgent` from `plugins.browser_agent.agent`, so both paths refer
to the same class. Move to the plugin path anyway so your code does not depend
on the deprecated layer:

```python
from plugins.browser_agent import BrowserAgent
```

---

### Issue 2: Plugin not loaded

**Symptom**: the plugin's routes answer `404` (or `503` with
`Plugin 'browser_agent' failed to activate.`) and the log shows:

```txt
Refusing to auto-activate plugin browser_agent: not in the enabled discovery set (disabled or absent from config)
```

**Solution**: enable the plugin in `configs/plugins.yaml`:

```yaml
browser_agent:
  enabled: true
```

---

### Issue 3: Circular import errors

**Error**:

```python
ImportError: cannot import name 'X' from partially initialized module
```

**Solution**: Check for circular dependencies. Moved modules may have exposed import cycles. Use `TYPE_CHECKING` for type hints:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plugins.browser_agent import BrowserAgent
```

---

## Rollback Procedure

If migration fails, you can rollback:

1. **Restore backup**:

   ```bash
   git reset --hard HEAD^
   ```

2. **Pin to v0.3.x**:

   ```bash
   pip install baselith-core==0.3.0
   ```

3. **Report issues**:
   - Open issue: <https://github.com/baselithcore/baselithcore/issues>
   - Include error logs and migration report

---

## Getting Help

- **Documentation**: <https://docs.baselithcore.xyz>
- **GitHub Issues**: <https://github.com/baselithcore/baselithcore/issues>
- **Discord Community**: <https://discord.gg/baselithcore>
- **Migration Support**: <support@baselithcore.xyz>

---

## FAQ

### Q: Will my v0.3.x plugins break in v0.4.0?

**A**: No, plugin interfaces remain unchanged. You may need to update imports if your plugin uses moved modules.

### Q: Can I stay on v0.3.x?

**A**: You can pin `baselith-core==0.3.0`, but releases are cut from `main` only (semantic-release, `.releaserc`); there is no maintenance branch for v0.3.x, so fixes and features land exclusively on the current line.

### Q: Do I need to migrate immediately?

**A**: No. The `core.*` paths remain as compatibility shims, so existing imports keep working; migrate at your own pace.

### Q: Will v0.4.0 break my application?

**A**: Only if you directly import moved modules. If you use core infrastructure correctly, migration is minimal.

### Q: How long does migration take?

**A**: For most applications, 1-2 hours: the change is a find-and-replace on imports plus enabling the plugins in `configs/plugins.yaml`.

---

**Last Updated**: 2026-09-04
**Applies to**: BaselithCore v0.3.0 → v0.4.0 (verified against v0.29.0)
