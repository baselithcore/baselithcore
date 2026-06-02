---
title: Registries & Exceptions
description: Shared domain-agnostic primitives — generic registry base and the framework exception hierarchy
---

<!-- markdownlint-disable-file MD025 -->

# Registries & Exceptions

Two small, domain-agnostic primitives that core and every plugin should reuse
instead of reimplementing: a generic registry base class and a single exception
hierarchy. Both live in the Sacred Core because they carry no domain logic.

---

## BaseRegistry

`core.registries.BaseRegistry[T]` is a thread-safe, name-keyed registry. It
replaces the half-dozen near-identical `register` / `get` / `list` / `remove`
classes that previously lived inside individual plugins (agent registries, skill
registries, document-source registries, extractor registries, …).

### Key resolution

When you register an item, its key is resolved in this order:

1. an explicit `name=` argument, else
2. the `key=` callable supplied at construction, else
3. the item's `.name` attribute.

If none yields a non-empty string, registration raises `KeyError`.

### API

| Method | Description |
| ------ | ----------- |
| `register(item, name=None, *, overwrite=True) -> str` | Store `item`; returns the resolved key. `overwrite=False` raises `DuplicateRegistrationError` on a clash. |
| `get(name) -> T \| None` | Lookup, or `None` if absent. |
| `require(name) -> T` | Lookup, or raise `ItemNotFoundError`. |
| `remove(name) -> bool` | Delete; `True` if something was removed. |
| `list(predicate=None) -> list[T]` | All items, optionally filtered. |
| `names() -> list[str]` | All keys. |
| `clear()` | Drop everything. |
| `name in reg`, `len(reg)`, `iter(reg)` | Container protocol support. |

### Example

```python
from core.registries import BaseRegistry
from core.exceptions import DuplicateRegistrationError

class Handler:
    def __init__(self, name: str): self.name = name

handlers: BaseRegistry[Handler] = BaseRegistry()
handlers.register(Handler("search"))             # keyed by .name -> "search"
handlers.register(obj, name="custom")            # explicit key

# key= for items without a usable .name attribute
tagged: BaseRegistry[Handler] = BaseRegistry(key=lambda h: h.name.upper())

try:
    handlers.register(Handler("search"), overwrite=False)
except DuplicateRegistrationError:
    ...
```

All operations are guarded by an `RLock`, so a registry can be shared across
threads (e.g. hot-reload worker + request handlers).

---

## Exception Hierarchy

`core.exceptions` defines a shallow, generic tree rooted at `BaselithError`.
Plugins and domain code should subclass the closest family rather than raising
bare `Exception` / `RuntimeError`, so callers and observability can distinguish
failure classes.

```text
BaselithError
├── PluginError
│   ├── PluginInitError          # failed during initialize()
│   ├── PluginConfigError         # config failed its JSON Schema
│   ├── PluginIntegrityError      # signing/integrity verification failed
│   └── PluginDependencyError     # core-version / plugin-dependency unsatisfied
└── RegistryError
    ├── DuplicateRegistrationError  # name already registered (overwrite off)
    └── ItemNotFoundError           # required lookup missed
```

Domain-specific error types belong in the plugin that owns the domain — subclass
the relevant family here so they remain catchable as `BaselithError`.

```python
from core.exceptions import PluginConfigError

class MyPluginBadEndpoint(PluginConfigError):
    """Raised when the configured endpoint URL is unreachable."""
```

---

## See also

- [Plugin System](plugins.md) — the `setup_app_middleware` hook and load-time
  admission gates use these primitives.
- [Dependency Injection](di.md) — the DI container's lazy registry is a
  specialised, DI-aware cousin of `BaseRegistry`.
