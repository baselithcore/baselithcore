# Best Practices & Framework Dogmas

To ensure your agents and plugins are high-quality, performant, and maintainable, follow these "Golden Rules" and implementation patterns.

---

## 1. Sacred Core

The `core/` directory is agnostic. Never put domain-specific logic there.

- ❌ **Bad**: Adding `process_jira_ticket()` to `core.utils`.
- ✅ **Good**: Creating a `jira-plugin` that implements the logic.

---

## 2. Plugin-First Architecture

Everything non-essential to the framework's infrastructure should be a plugin.

- **Modularity**: Keep plugins focused on a single responsibility.
- **Independence**: Plugins should not strictly depend on each other unless necessary.

---

## 3. The 4 Dogmas of Baselith-Core

### I. Async By Default

All I/O operations (Database, HTTP, LLM calls) **MUST** be asynchronous.

```python
# ✅ YES
async with httpx.AsyncClient() as client:
    resp = await client.get(url)

# ❌ NO
resp = requests.get(url)
```

### II. Explicit Lifecycle

Implement `LifecycleMixin` correctly. Resources should be setup in `_do_startup` and cleaned up in `_do_shutdown`.

```python
class MyAgent(LifecycleMixin, AgentProtocol):
    async def _do_startup(self):
        self.client = await create_client()

    async def _do_shutdown(self):
        await self.client.close()
```

### III. Dependency Injection (DI)

Use the global DI container to resolve services like LLM or VectorStores.

```python
from core.di import resolve
from core.interfaces import LLMServiceProtocol

llm = resolve(LLMServiceProtocol)
```

### IV. Agent Protocol

All agents must implement `AgentProtocol` to be pluggable into the orchestrator.

```python
async def execute(self, input: str, context: Optional[dict] = None) -> str:
    # Logic here
    return result
```

---

## 4. Gold Standard Implementation

Reference the [Gold Standard Example](/baselith-core/examples/baselith_standard_example.py) for the most complete implementation of these patterns.

### Key Features of a Gold Standard Agent

1. **Inherits** from `LifecycleMixin` and `AgentProtocol`.
2. **Accepts** `agent_id` and `config` in `__init__`.
3. **Validates** state before execution.
4. **Uses** structured logging.
5. **Observes** tenant contexts if applicable.

---

## 5. Security & Resilience

- **Fail Gracefully**: Use try/except blocks around external service calls.
- **No Secrets**: Use environment variables and the `config` system.

---

## 6. Detecting Sacred Core Violations

The Sacred Core rule is the foundation of BaselithCore's architecture. Here's how to detect and fix violations:

### Common Violation Patterns

#### ❌ **Domain-Specific Agents in Core**

**Example**: `core/agents/browser_agent.py`, `core/agents/coding/`

**Why it's wrong**: These implement specific agent behaviors (browser automation, code generation), not generic orchestration infrastructure.

**Fix**: Move to `plugins/browser_agent/` and `plugins/coding_agent/`

**Impact**: Browser and coding agents are application-specific features, not core framework capabilities.

---

#### ❌ **Document Processing Logic in Core**

**Example**: `core/doc_sources/web.py`, `core/doc_sources/readers.py`

**Why it's wrong**: Web crawling, PDF parsing, and OCR are domain-specific document workflows, not agnostic infrastructure.

**Fix**: Move to `plugins/document_sources/`

**Rationale**: Document processing strategies vary by application. Core should provide the *interface* for document sources, not specific implementations.

---

#### ❌ **Specific Scrapers in Core**

**Example**: `core/scraper/extractors/`, `core/scraper/fetchers/`

**Why it's wrong**: HTML/DOM extraction and web-specific fetching are not agnostic infrastructure.

**Fix**: Move to `plugins/web_scraper/`

**Rationale**: Scraping is a specialized capability. Some apps need it, others don't.

---

#### ❌ **Application Endpoints in Core**

**Example**: `core/routers/chat.py`, `core/routers/feedback.py`, `core/routers/admin.py`

**Why it's wrong**: These implement specific API endpoints for chat/feedback/admin applications.

**Fix**: Move to application layer or `plugins/api_routers/`

**Rationale**: API endpoints are application-specific. Core provides routing infrastructure, not specific routes.

---

#### ❌ **Hard-coded Domain Personas**

**Example**: `core/personas/defaults.py` with `HELPFUL_ASSISTANT`, `TECHNICAL_EXPERT`

**Why it's wrong**: These are domain-specific personality templates, not infrastructure.

**Fix**: Move default personas to `plugins/personas/` or application config

**Rationale**: Persona definitions are application-specific. Core provides the persona *manager*, not specific personas.

---

### Detection Tools

Run these commands to detect potential violations:

```bash
# Check for hardcoded domain prompts
rg "SYSTEM_PROMPT|user_agent|crawl" core/ --type py

# Check for specific integrations that should be plugins
rg "playwright|tesseract|beautifulsoup|selenium" core/ --type py

# Check for re-exports from plugins (circular dependency)
rg "from plugins\." core/ --type py

# Find domain-specific file formats
rg "\.pdf|\.docx|\.xlsx|\.html" core/ --type py --ignore-case

# Run the enforced architecture boundary check
python scripts/check_architecture_boundaries.py
```

### Enforced Guardrails

These rules are no longer documentation-only. The framework now enforces them through dedicated checks:

- `python scripts/check_architecture_boundaries.py`
- `python scripts/check_official_plugin_typing.py`
- `python scripts/check_core_resilience_typing.py`

These checks are wired into CI and pre-commit for the supported slices of the codebase.

### Architecture Validation Checklist

When reviewing code for Sacred Core compliance:

- [ ] **Does this solve a domain-specific problem?** → Plugin
- [ ] **Does this implement business logic?** → Plugin
- [ ] **Could an app be built without this?** → Plugin
- [ ] **Is this tied to specific file formats/protocols?** → Probably Plugin
- [ ] **Does this provide infrastructure for plugins?** → Core

**Golden Question**: *"If I built a completely different application (e.g., IoT monitoring instead of document analysis), would I need this module?"*

- **No** → It's domain-specific → Plugin
- **Yes** → It's infrastructure → Core

---

### Migration Checklist

When moving code from `core/` to `plugins/`:

1. [ ] **Create plugin structure**: `plugins/my-plugin/`
2. [ ] **Implement plugin interface**: `FlowHandlerMixin`, `LifecycleMixin`
3. [ ] **Update imports** across codebase
4. [ ] **Move tests**: `tests/unit/core/` → `tests/unit/plugins_tests/`
5. [ ] **Update documentation** references
6. [ ] **Add plugin to `configs/plugins.yaml`**
7. [ ] **Test plugin isolation**: Can be disabled without breaking core
8. [ ] **Update type hints** and remove circular imports

---

### Migration Status

The highest-priority Sacred Core violations have already been migrated out of `core/` and now live in official plugins with backward-compatible shims where necessary:

| Legacy Area | Current State | Canonical Location |
|-------------|---------------|--------------------|
| `core/agents/` | Compatibility shims | `plugins/browser_agent/`, `plugins/coding_agent/` |
| `core/doc_sources/` | Compatibility shims | `plugins/document_sources/` |
| `core/scraper/` | Compatibility shims | `plugins/web_scraper/` |
| `core/routers/` | Compatibility shims | `plugins/api_routers/` |

The remaining work is mainly incremental quality hardening, not large-scale Sacred Core extraction.

**See**: [Migration Guide](migration-guide.md) for older upgrade paths.

---

### Best Practices for Contributors

When adding new functionality:

1. **Default to Plugin**: If unsure, implement as plugin first
2. **Propose Core Changes**: Open an issue for discussion before adding to core
3. **Justify Agnosticism**: Clearly explain why new core code is domain-agnostic
4. **Review Examples**: Study existing core modules for patterns

**Remember**: It's easier to move code from plugins to core later than to extract domain logic from core after it's embedded.
