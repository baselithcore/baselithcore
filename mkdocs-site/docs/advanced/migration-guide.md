# v0.3.x → v0.4.0 Migration Guide

## Overview

BaselithCore v0.4.0 completes the architectural refactoring to enforce the **Sacred Core** principle. This guide helps users migrate plugins and applications from v0.3.x to v0.4.0.

---

## Breaking Changes

### Moved Modules

The following modules have been moved from `core/` to `plugins/` to enforce architectural compliance:

| v0.3.x Location | v0.4.0 Location | Migration Impact |
|----------------|-----------------|------------------|
| `core.agents.browser_agent` | `plugins.browser_agent` | Update imports |
| `core.agents.coding` | `plugins.coding_agent` | Update imports |
| `core.doc_sources` | `plugins.document_sources` | Update imports |
| `core.scraper` | `plugins.web_scraper` | Update imports |
| `core.routers.*` | Application layer | Removed from core |
| `core.chat` (partial) | `plugins.rag_chat` | Refactored |
| `core.personas.defaults` | `plugins.default_personas` | Update imports |

---

## Import Updates Required

### Before (v0.3.x)

```python
from core.agents.browser_agent import BrowserAgent
from core.agents.coding.agent import CodingAgent
from core.doc_sources.web import WebDocumentSource
from core.doc_sources.readers import PDFReader
from core.scraper import Scraper
from core.routers.chat import chat_router
from core.personas.defaults import HELPFUL_ASSISTANT
```

### After (v0.4.0)

```python
from plugins.browser_agent import BrowserAgent
from plugins.coding_agent import CodingAgent
from plugins.document_sources.web import WebDocumentSource
from plugins.document_sources.readers import PDFReader
from plugins.web_scraper import Scraper
# Routers moved to application layer - import from your app
from plugins.default_personas import HELPFUL_ASSISTANT
```

---

## Configuration Changes

### Plugin Configuration

Update `configs/plugins.yaml` to explicitly load migrated plugins:

**Before (v0.3.x)** - Plugins were optional:
```yaml
plugins:
  # Optional additional plugins
```

**After (v0.4.0)** - Explicitly enable needed plugins:
```yaml
plugins:
  # Core functionality now in plugins
  - name: browser_agent
    enabled: true
    config:
      timeout: 30

  - name: coding_agent
    enabled: true
    config:
      sandbox: docker

  - name: document_sources
    enabled: true
    config:
      formats: [pdf, docx, html]
      ocr_backend: tesseract

  - name: web_scraper
    enabled: true
    config:
      max_depth: 3
      respect_robots: true
```

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

**v0.4.0** - Routers are application-specific:
```python
from fastapi import FastAPI
from your_app.routers import chat, feedback, admin

app = FastAPI()
app.include_router(chat.router)
app.include_router(feedback.router)
app.include_router(admin.router)
```

---

## Deprecation Timeline

| Version | Date | Status |
|---------|------|--------|
| **v0.3.0** | 2026-03 | Current release, imports work with deprecation warnings |
| **v0.3.5** | 2026-04 | Migration tools and documentation released |
| **v0.4.0** | 2026-05 | Clean architecture, old imports removed |

---

## Migration Tools

### Automated Migration Assistant

Run the migration assistant to update your codebase:

```bash
# Check for deprecated imports in your code
baselith migrate check

# Auto-fix imports (creates backup first)
baselith migrate fix

# Verify migration success
baselith migrate verify
```

### Manual Migration

If you prefer manual migration:

1. **Find deprecated imports**:
   ```bash
   grep -r "from core.agents" your_project/
   grep -r "from core.doc_sources" your_project/
   grep -r "from core.scraper" your_project/
   ```

2. **Update imports** using find-replace:
   - `from core.agents` → `from plugins.browser_agent` or `plugins.coding_agent`
   - `from core.doc_sources` → `from plugins.document_sources`
   - `from core.scraper` → `from plugins.web_scraper`

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
- ⚠️ **Imports from moved modules**: Update imports

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
- [ ] Review current imports: `baselith migrate check`
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

### Issue 1: ImportError after migration

**Error**:
```python
ImportError: cannot import name 'BrowserAgent' from 'core.agents'
```

**Solution**: Update import to new location:
```python
from plugins.browser_agent import BrowserAgent
```

---

### Issue 2: Plugin not loaded

**Error**:
```
PluginNotFoundError: Plugin 'browser_agent' not found
```

**Solution**: Add plugin to `configs/plugins.yaml`:
```yaml
plugins:
  - name: browser_agent
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
   - Open issue: https://github.com/baselithcore/baselithcore/issues
   - Include error logs and migration report

---

## Getting Help

- **Documentation**: https://docs.baselithcore.xyz
- **GitHub Issues**: https://github.com/baselithcore/baselithcore/issues
- **Discord Community**: https://discord.gg/baselithcore
- **Migration Support**: support@baselithcore.xyz

---

## FAQ

### Q: Will my v0.3.x plugins break in v0.4.0?

**A**: No, plugin interfaces remain unchanged. You may need to update imports if your plugin uses moved modules.

### Q: Can I stay on v0.3.x?

**A**: Yes, v0.3.x will receive security patches through 2026. However, new features will only be added to v0.4.0+.

### Q: Do I need to migrate immediately?

**A**: No. v0.3.5 will include migration tools and extended deprecation warnings. Plan migration for v0.4.0 release.

### Q: Will v0.4.0 break my application?

**A**: Only if you directly import moved modules. If you use core infrastructure correctly, migration is minimal.

### Q: How long does migration take?

**A**: For most applications: 15-30 minutes with automated tools, 1-2 hours manually.

---

**Last Updated**: 2026-03-17
**Applies to**: BaselithCore v0.3.0 → v0.4.0
