---
title: Creating Plugins
description: Step-by-step tutorial for creating plugins
---

<!-- markdownlint-disable-file MD046 MD025 -->

A practical guide to plugin development.

!!! tip "Importing from `core.plugins`"
    Nothing changes for a plugin author: `from core.plugins import Plugin,
    PluginMetadata, SkillResult` works exactly as before. The package now
    resolves each export on first access rather than importing every submodule
    up front, so a plugin that needs only the result envelope no longer pays
    for the registry, the loader and the hot-reload controller. See
    [import-time laziness](../advanced/lazy-loading.md#import-time-laziness).

---

## 1. Scaffold with CLI

Use the CLI to generate the plugin structure:

```bash
baselith plugin create my-plugin --type agent
baselith plugin create --interactive  # Launch the wizard
```

Expected output:

```text
✅ Created plugin at plugins/my-plugin

Files created:
  - plugins/my-plugin/manifest.json
  - plugins/my-plugin/__init__.py
  - plugins/my-plugin/plugin.py
  - plugins/my-plugin/agent.py
```

This command creates a plugin skeleton with the files required for the selected type
(the `router` type emits `router.py` instead of `agent.py`; `graph` emits only
`manifest.json`, `__init__.py` and `plugin.py`). A `README.md` is not generated.

!!! info "Scaffold Output"
    The current CLI scaffold still emits `manifest.json`. The framework also supports `manifest.yaml`, which remains the preferred format for manually curated or packaged plugins.

!!! tip "Plugin Types"
    - `agent`: Creates a plugin with flow handlers and agent logic
    - `router`: Creates a plugin focused on API endpoints
    - `graph`: Creates a plugin that extends the knowledge graph schema

---

## 1b. Scaffold with Backstage (Alternative)

If you have enabled the [Backstage Integration](backstage.md), you can create new plugins directly from your developer portal:

1. Navigate to your **Backstage Create** page.
2. Search for the **Baselith Plugin Template**.
3. Fill in the required parameters:
    * `pluginName`: Unique name for your plugin.
    * `description`: What your plugin does.
    * `owner`: The owner for this plugin component.
4. Run the scaffolding job.

Backstage will use the official framework skeleton to generate a production-ready plugin structure and automatically register it in the catalog.

!!! tip "Governance"
    Using Backstage for scaffolding is the recommended approach for large teams to ensure consistent plugin structures and proper ownership from day one.

---

## 2. Declare Metadata

Every plugin must declare its metadata in a manifest file next to `plugin.py`. The
loader looks for, in order of preference:

1. **`manifest.yaml`** (Recommended): Clean and readable YAML (`manifest.yml` is
   accepted too).
2. **`manifest.json`**: Standard JSON format.

A directory without one of these is skipped at discovery. There is no Python-side
alternative: `Plugin.metadata` is a `cached_property` that reads the manifest and
returns a `PluginMetadata`, so `self.metadata.name` / `self.metadata.version` are
available without declaring anything in code.

### 2a. The Manifest File (Recommended)

The `manifest.yaml` file contains the plugin's identity and description:

```yaml title="plugins/my-plugin/manifest.yaml"
name: "my-plugin"
version: "1.0.0"
description: "My custom plugin"
author: "Your Name"
tags: ["agent", "my-plugin"]
category: "ai"
required_resources: ["gpu", "internet"]
environment_variables: ["API_KEY"]
```

Alternatively, use `manifest.json`:

```json title="plugins/my-plugin/manifest.json"
{
    "name": "my-plugin",
    "version": "1.0.0",
    "description": "My custom plugin",
    "author": "Your Name",
    "tags": ["agent", "my-plugin"],
    "category": "AI",
    "environment_variables": ["API_KEY"]
}
```

```python title="plugins/my-plugin/plugin.py"
from typing import Any

from core.plugins import AgentPlugin

from .agent import MyAgent


class MyPlugin(AgentPlugin):
    async def initialize(self, config: dict[str, Any]) -> None:
        await super().initialize(config)  # stores config, marks the plugin initialized

    def create_agent(self, service: Any, **kwargs) -> MyAgent:
        return MyAgent(agent_id="my-plugin-agent", config=self._config)

    # ... rest of implementation ...
```

`AgentPlugin` already extends `Plugin`, so subclass the mixin alone — `class
MyPlugin(Plugin, AgentPlugin)` is an MRO `TypeError`.

### Metadata Fields

| Field                   | Required | Description                                                  |
| ----------------------- | -------- | ------------------------------------------------------------ |
| `name`                  | Yes      | Unique plugin identifier — **must equal the plugin's directory name** |
| `display_name`          | No       | Human-readable name (e.g. `CV Intake`) shown by consoles and used as the Backstage Component title. **Presentation only**: `name` keeps keying routes (`/api/<name>`), the `configs/plugins.yaml` entry, env prefixes, stored data and grants — so to change how a plugin reads, set `display_name`; never rename `name`. Absent ⇒ a title derived from `name` (`my_plugin` → "My Plugin"). |
| `version`               | Yes      | Semantic version (e.g., `1.2.3`)                             |
| `description`           | Yes      | Brief description of plugin functionality                    |
| `author`                | No       | Plugin author name or organization                           |
| `tags`                  | No       | Keywords for categorization and search                       |
| `category`              | No       | Primary category (e.g., `ai`, `security`, `utility`)         |
| `required_resources`    | No       | List of resources (e.g., `gpu`, `internet`, `storage`)       |
| `environment_variables` | No       | Required environment variables, reported by `baselith doctor` — a list of names, or a list of mappings with `name`, `description` and `required` (only the names are used). Also **widens the plugin `.env` allowlist** to these exact keys, for names the plugin does not own (`SLACK_SIGNING_SECRET`); framework-protected keys can never be declared this way. See [the policy](../core-modules/plugins.md#two-gates-namespace-allowlist-then-protected-key-denylist) |
| `python_dependencies`   | No       | List of required Python packages (`pip install` format)      |
| `plugin_dependencies`   | No       | Map of plugin name to version constraint (e.g. `my_plugin: '>=2.0.0'`). Declares that this plugin needs another one, and **orders initialization** — see below |
| `tenancy`               | No       | Data-scoping model: `shared` (default) keys storage by the deployment tenant; `personal` keys it by the authenticated user (1 user = 1 tenant). Resolve via `self.tenant_key()`. See [Multi-Tenancy](../advanced/multi-tenancy.md#per-plugin-tenancy-personal-vs-shared). |
| `entry_point`           | No       | Which class to instantiate, as `module:Class` (also `:Class` or a bare `Class`), resolved inside the plugin's own package. Required when `plugin.py` exposes more than one concrete `Plugin` subclass — ambiguity is a hard error, not a guess. A *file* name (`__init__.py`, `plugin.py`, `src/plugin.py` — the marketplace spelling) names no class, so it is ignored and the class is resolved by inspecting the module; ambiguity there is still refused. |
| `entrypoint`            | No       | Legacy spelling of `entry_point`, accepted for manifests written before the canonical key existed. `entry_point` wins when both are present; new plugins declare `entry_point` only. |
| `frontend`              | For a UI | Frontend build contract read by `baselith plugin add <repository> --docker` and by `baselith doctor`: a mapping with `path` (relative to the plugin directory, default `ui`), `package_manager` (`npm`, `pnpm` or `yarn`), `build_command` and `output_dir` (relative to `path`, default `dist`), or `false` to disable frontend detection. Declare it whenever the plugin ships a UI — without it the installer falls back to guessing and `doctor` cannot tell a missing build from a plugin that has no frontend at all. See [Packaging › Docker installation contract](packaging.md#docker-installation-contract). |
| `health_endpoint`       | No       | Local HTTP path probed after a Docker installation. It must answer 200 with no authentication and no redirect; without it the installer probes the default `/<name>/`. |

!!! danger "Unknown manifest keys are refused"
    The manifest schema (`core.plugins.manifest_model.PluginManifestModel`) is
    `extra="forbid"`. A key outside the table above — a typo, or a key from another
    framework — fails validation and the plugin is **refused in every environment**,
    with a did-you-mean hint:

    ```text
    manifest.yaml: unknown manifest key(s): 'min_core_verison' (did you mean
    'min_core_version'?). Remove the key or fix the spelling — the loader ignores
    nothing.
    ```

    A plugin with **no** manifest at all is the legacy shape and still loads. A
    plugin whose manifest is present but broken never does — a manifest that will
    not parse declares no `permissions` and no version bounds, so loading it anyway
    would grant more authority than its author asked for.
    See [Packaging › Manifest Fields](packaging.md#manifest-fields) for the full key
    list.

#### Your own keys: the `x-` namespace

The strict schema covers the keys the **core** interprets, which leaves nowhere to
put data only your plugin reads. Prefix it with `x-`:

```yaml title="plugins/my-plugin/manifest.yaml"
name: "my-plugin"
version: "1.0.0"
description: "My custom plugin"

x-control:
  mode: strict
  refresh_seconds: 30
```

```python
control = self.metadata.extension("control")        # prefix optional on read
mode = (control or {}).get("mode", "lenient")
```

The core carries these verbatim and never interprets them. Two consequences worth
knowing before you use one:

* a **typo in a known key is still refused** — `x-` is checked before validation
  and no core key carries the prefix, so `min_core_verison` cannot slip through as
  an extension;
* an extension is **inside the integrity digest**, so editing `x-control` on a
  signed plugin breaks its `integrity_sha256` exactly like editing `permissions`
  does. Re-sign afterwards (see [Signing](#signing)).

Full rules — key grammar, case sensitivity, shadowing, cross-plugin collisions —
in [Packaging › Vendor extensions](packaging.md#vendor-extensions).

!!! danger "Name must equal the directory name"
    The manifest `name` is the plugin's **canonical key** — the loader, the lifecycle
    manager, the registry and route mounting all use it. `configs/plugins.yaml` lookup
    is the one place with slack: it tries the manifest name, the directory name, and
    the directory name with `-`/`_` swapped, so dir `weather_agent` with
    `name: weather-agent` still finds its config entry. Anything else (dir
    `baselithbot` with `name: BaselithBot`, or two different words) does not resolve:
    the config entry is never matched and the plugin runs as if it had none.

    Hot reload (`POST /api/plugins/{name}/enable|disable|reload` and the startup
    auto-activation) resolves the same way: a directory name is mapped to the
    manifest name before any state lookup, so enabling `weather_agent` and
    disabling it again address the one plugin registered as `weather-agent`.

    What the directory uses *is* the rule: dir `weather_agent` → `name: weather_agent`;
    dir `example-plugin` → `name: example-plugin`. Keep them byte-identical and
    lowercase — static/SPA assets are only mounted for names matching
    `^[a-z0-9][a-z0-9._-]{0,63}$`. If another plugin declares `plugin_dependencies`
    against yours, the dependency key must be this exact string.

!!! tip "`plugin_dependencies` decides who initializes first"
    The loader topologically sorts plugins before initializing them, so a plugin
    named in your `plugin_dependencies` is guaranteed to have completed
    `initialize()` — and therefore to have registered its services in the
    `ServiceRegistry` — before yours runs. Declare it whenever you resolve
    another plugin's service, even lazily inside a request: without the
    declaration the order is arbitrary, and your guard may observe a service
    that is simply not registered yet.

    Both manifest spellings feed the sort: the current `plugin_dependencies`
    map and the legacy `dependencies` list of bare names. Prefer
    `plugin_dependencies` — it also carries the version constraint, ordered
    per PEP 440 where expressible and semver §11 precedence otherwise (see
    [Version compatibility](../core-modules/plugins.md#load-time-admission-gates)).

!!! warning "An absent dependency now skips your plugin"
    The topological **sort** ignores a dependency that is not present, so it never
    blocks ordering — but the compat **gate** does not: it is fail-closed, and a
    `plugin_dependencies` entry missing from the load set refuses your plugin.
    Declare only what you genuinely need; a truly optional integration should be
    resolved defensively at call time rather than declared. Operators can downgrade
    the gate to warn-only with `BASELITH_ENFORCE_PLUGIN_COMPAT=false` while a
    manifest is corrected. See
    [Packaging › Dependencies](packaging.md#dependencies).

---

## 3. Implement the Agent

The agent logic should reside in `agent.py` and implement the `AgentProtocol`:

```python title="plugins/my-plugin/agent.py"
from typing import Optional, Dict, Any
from core.lifecycle import LifecycleMixin, AgentState
from core.orchestration.protocols import AgentProtocol
import logging

logger = logging.getLogger(__name__)

class MyAgent(LifecycleMixin, AgentProtocol):
    def __init__(self, agent_id: str, config: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.agent_id = agent_id
        self.config = config or {}

    async def _do_startup(self) -> None:
        logger.info(f"Agent {self.agent_id} starting up...")

    async def execute(self, input: str, context: Optional[Dict[str, Any]] = None) -> str:
        if self.state != AgentState.READY:
            return "Agent not ready."

        return f"Processed: {input}"
```

---

---

## 4. Register Agents and Intents

The orchestrator discovers agents and routes requests based on intent patterns:

```python title="plugins/my-plugin/plugin.py"
class MyPlugin(AgentPlugin):
    # ... initialize ...

    def create_agent(self, service: Any, **kwargs) -> MyAgent:
        """Required factory (abstract on ``AgentPlugin``); ``service`` is the chat service."""
        return MyAgent(agent_id="my-plugin-agent", config=self._config)

    def get_agents(self) -> list[Any]:
        """Agent objects to register at load time (a list, not a dict of classes)."""
        return [MyAgent(agent_id="my-plugin-agent", config=self._config)]

    def get_intent_patterns(self) -> list[dict[str, Any]]:
        """
        Define patterns that trigger this plugin's intents.
        """
        return [
            {
                "name": "my_intent",
                "patterns": ["keyword1", "keyword2", "analyze"],
                "priority": 100
            }
        ]

    def get_ui_tabs(self) -> list:
        """
        Register navigation items in the admin sidebar.
        """
        return [
            {"id": "my-plugin-tab", "label": "My Plugin"}
        ]

    def get_mcp_tools(self) -> list:
        """
        Expose MCP tools to the core MCP server.
        """
        return [
            {
                "name": "my_tool",
                "description": "A custom tool",
                "input_schema": {"type": "object", "properties": {}},
                "handler": self.handle_tool,  # async, called with the tool arguments
                "category": "read_only",  # omitted = "destructive"
            }
        ]
```

### Intent Pattern Structure

| Field      | Type        | Description                                         |
| ---------- | ----------- | --------------------------------------------------- |
| `name`     | `str`       | Unique intent identifier — entries without it are silently dropped |
| `patterns` | `list[str]` | Keywords or phrases that trigger this intent        |
| `priority` | `int`       | Routing priority (higher = preferred). Default: 0   |

### Registration Hooks

The `Plugin` interface provides several hooks for registering components:

| Method | Returns | Description |
|--------|---------|-------------|
| `get_agents` | `list` | AI agents for the orchestrator |
| `get_routers` | `list` | FastAPI routers for the API |
| `get_intent_patterns` | `list` | NLP patterns for routing |
| `get_ui_tabs` | `list` | Navigation items for the Admin UI |
| `get_mcp_tools` | `list` | Tools for Model Context Protocol |
| `get_flow_handlers` | `dict` | Intent name → handler object with `async handle(query, context)` (or an async callable with that signature) |
| `get_entity_types` | `list` | Knowledge Graph node types |

!!! tip "Routing"
    The orchestrator uses these patterns to identify when a user request should be handled by your plugin's agents.

`get_mcp_tools()` entries reach the HTTP-mounted MCP server (`/mcp`) when the plugin
activates — at startup or through hot reload — and are withdrawn when it is
disabled, with a `tools/list_changed` notification each time. An entry without
`name` or `handler` is skipped. `category` (`read_only`, `mutating`, `destructive`,
`external_side_effect`) feeds the autonomy gate; leaving it out registers the tool
as `destructive`. See [MCP › Plugin tools](../core-modules/mcp.md#plugin-tools).

---

## 5. Add API Endpoints (Optional)

Expose custom API endpoints by returning FastAPI routers:

```python title="plugins/my-plugin/router.py"
from fastapi import APIRouter
from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str


# No prefix here: the plugin prefix (/api/my-plugin) is added at mount time.
router = APIRouter(tags=["My Plugin"])


@router.get("/status")
async def status():
    return {"status": "ok"}


@router.post("/process")
async def process(request: QueryRequest):
    return {"processed": request.query}
```

In your `plugin.py`, add the `RouterPlugin` mixin and implement its abstract
`create_router()` (`get_routers()` defaults to `[self.create_router()]`; override it
only to expose several routers):

```python title="plugins/my-plugin/plugin.py"
from fastapi import APIRouter

from core.plugins import AgentPlugin, RouterPlugin


class MyPlugin(AgentPlugin, RouterPlugin):
    # ... agent methods ...

    def create_router(self) -> APIRouter:
        from .router import router

        return router
```

### Automatic API Registration

When a plugin implements `create_router()`, its endpoints are automatically:

* Mounted under `get_router_prefix()` — `/api/{plugin-name}` by default. The prefix
  is passed verbatim to `include_router`, and any `prefix=` set on the `APIRouter`
  is appended after it: `APIRouter(prefix="/my-plugin")` would end up at
  `/api/my-plugin/my-plugin/status`, so leave the router prefix empty. Keep
  `get_router_prefix()` plugin-specific: a bare core-owned segment such as
  `/api` cannot express ownership, so the registry logs a warning (once) and
  leaves requests under it unattributed for the plugin context.
* Included in OpenAPI documentation
* Tagged for easy discovery in Swagger UI

Routes are not authenticated by default: add
`dependencies=[Depends(require_user)]` (from `core.middleware`) to the `APIRouter`
to require an authenticated user, as `plugins/example-plugin/router.py` does.

**Accessing endpoints:**

```bash
# Health check
curl http://localhost:8000/api/my-plugin/status

# Direct processing
curl -X POST http://localhost:8000/api/my-plugin/process \
  -H "Content-Type: application/json" \
  -d '{"query": "test"}'
```

---

## 6. Configure Plugin

Define plugin-specific configuration in `configs/plugins.yaml`:

```yaml title="configs/plugins.yaml"
my-plugin:
  enabled: true
  custom_setting: "value"
  api_timeout: 30
  max_retries: 3
```

The file is a **flat mapping keyed by plugin name**: there is no `plugins:` wrapper
and no `config:` sub-key (a nested layout is never matched, and the plugin runs as if
it had no entry). The whole entry — `enabled` included — is passed to
`initialize(config)`.

Which plugins run follows one rule (`core.plugins.config_file.plugin_enabled`),
shared by discovery, startup auto-activation and `baselith plugin sync`: with no
config file every discovered plugin runs; once the file lists anything, only the
listed plugins run, and an entry runs unless it says `enabled: false` — an entry
without an `enabled` key counts as enabled. `PLUGIN_CONFIG_PATH` (default
`configs/plugins.yaml`) points at a different file, which must resolve inside the
working directory. Values are literal; there is no `${VAR}` interpolation, so
secrets go in the plugin-local `.env` described below.

### Configuration Access

Access configuration through `get_config()` after `super().initialize()` has stored
it, and hand the agent what it needs:

```python
class MyPlugin(AgentPlugin):
    async def initialize(self, config: dict[str, Any]) -> None:
        await super().initialize(config)
        self.timeout = self.get_config("api_timeout", 10)

    def create_agent(self, service: Any, **kwargs) -> MyAgent:
        return MyAgent(agent_id="my-plugin-agent", config=self._config)
```

```python
class MyAgent(LifecycleMixin, AgentProtocol):
    def __init__(self, agent_id: str, config: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.timeout = (config or {}).get("api_timeout", 10)
```

!!! warning "State that must outlive a request cannot live in memory"

    A uvicorn worker is a separate process with its own memory. A plugin that
    keeps runs, jobs or sessions in a module-level dict has **one copy per
    worker**, and requests are balanced between them: a resource written by
    one request is missing from the next, which reads as data loss rather
    than as a deployment setting.

    The launcher records the worker count so children can see it:

    ```python
    from core.config import get_web_concurrency

    if get_web_concurrency() > 1 and backend == "memory":
        # refuse, warn loudly, or select the durable backend
        ...
    ```

    `get_web_concurrency()` returns 1 when the server is single-process, and
    never raises — it is safe on the boot path. Detect the combination at
    startup and say so; a user cannot diagnose it from a 404.

!!! tip "Plugin-scoped environment config"
    Plugin-specific environment keys belong in a **plugin-local** `.env`
    (`plugins/<name>/.env`), never in the repo-root `.env` — the root file is
    reserved for framework/core configuration, and mixing plugin keys into it
    creates cross-plugin confusion. Namespace every key with your plugin's
    prefix (e.g. `MYPLUGIN_*`) and load the file with the framework helper at
    module import, before your plugin reads its configuration:

    ```python title="plugins/my-plugin/plugin.py"
    from pathlib import Path

    from core.plugins.env import load_plugin_dotenv

    load_plugin_dotenv(Path(__file__).resolve().parent)
    ```

    ```text title="plugins/my-plugin/.env"
    MYPLUGIN_API_TIMEOUT=60
    MYPLUGIN_CUSTOM_SETTING=my_local_value
    ```

    Loading is additive and safe: existing process env always wins
    (`override=False`), a missing file is a no-op, and a malformed file never
    breaks host boot. The file is gitignored like every `.env`, so it doubles
    as the operator's documented, per-plugin config surface.

    **The namespace is enforced, not a convention.** Keys outside your plugin's
    `<DIRNAME>_` prefix are refused, and so are framework-global controls even
    inside it — the same policy applies whether the file is loaded by this
    helper or automatically by the plugin loader. A key that is legitimately
    named by a third party (`SLACK_SIGNING_SECRET`, `DISCORD_PUBLIC_KEY`) must
    be declared in the manifest's `environment_variables` list, which widens the
    allowlist to that exact key. See
    [Plugin-Specific Environment Variables](../core-modules/plugins.md#plugin-specific-environment-variables-env)
    for the full policy and the deprecated `BASELITH_PLUGIN_ENV_LEGACY_DENYLIST`
    opt-out.

    Every key the `.env` exports is recorded as yours, so the startup
    environment drift check never flags it as a misspelled core setting. If
    your code writes a variable into `os.environ` directly instead, declare it
    with `core.config.register_owned_env(name)` to get the same exemption.

---

## 6b. Create Your Database Schema (Optional)

If your plugin owns tables, create them in `init_schema()` — never in
`initialize()`:

```python title="plugins/my-plugin/plugin.py"
class MyPlugin(Plugin):
    async def init_schema(self, config: dict | None = None) -> None:
        """Create this plugin's tables. Runs at deploy time, as their owner."""
        async with get_connection() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "CREATE TABLE IF NOT EXISTS myplugin_notes ("
                "  id BIGSERIAL PRIMARY KEY,"
                "  tenant_id TEXT NOT NULL,"
                "  body TEXT NOT NULL)"
            )
```

Run it with `baselith plugin schema-init` (every enabled plugin) or
`baselith plugin schema-init --plugin my-plugin`. Leave the method out entirely
when your tables come from an Alembic migration — the default is a no-op.

Doing this at boot instead breaks any deployment that connects as a
least-privilege role for row-level security: it cannot `CREATE`, and if it
could, it would *own* the tables and PostgreSQL would exempt it from their
policies. See
[Schema is deploy work](../core-modules/plugins.md#schema-is-deploy-work-not-boot-work).

---

## 7. Test the Plugin

### Verify Plugin Loading

```bash
# List all loaded plugins
baselith plugin list

# Check specific plugin status
baselith plugin status --name my-plugin

# Verify dependencies
baselith plugin deps check my-plugin

# Visualize dependency tree
baselith plugin tree my-plugin
```

Expected output:

```text
✅ my-plugin (0.7.0)
  Status: Active
  Agents: MyAgent
  Endpoints: /api/my-plugin/status
```

### Reporting your own health

The framework only knows whether `initialize()` completed. Override the optional
async `health()` hook to report what only your plugin knows — a stale upstream, an
expired credential, a drained queue:

```python
from core.plugins import AgentPlugin, PluginHealth


class MyPlugin(AgentPlugin):
    async def health(self) -> PluginHealth:
        lag = await self._feed.seconds_behind()
        return PluginHealth(
            healthy=lag < 300,
            detail=f"feed {lag:.0f}s behind",
            data={"lag_seconds": lag},   # must be JSON-serialisable
        )
```

`await registry.check_health()` awaits the hook **only when you override it**, so
not implementing it costs nothing. A hook that raises marks your plugin unhealthy
and is logged — it never takes the health endpoint down. The synchronous
`registry.health_check()` is unchanged.

### Background tasks

Never start a bare `asyncio.create_task` from a plugin: it outlives your own
reload, so the new generation initializes while the old one's loop keeps touching
shared state. Spawn through the registry instead, and the task is owned by your
plugin — cancelled *and awaited* on unregister, reload and hot reload:

```python
from core.di import ServiceRegistry
from core.plugins import PluginRegistry


class MyPlugin(AgentPlugin):
    async def initialize(self, config: dict[str, Any]) -> None:
        await super().initialize(config)
        registry = ServiceRegistry.get(PluginRegistry)
        registry.spawn_task(self.metadata.name, self._poll_forever())
```

The teardown wait is bounded by `BASELITH_PLUGIN_TASK_CANCEL_TIMEOUT` (default
`10` seconds; `0` means cancel and do not wait). While teardown is in flight
`spawn_task` raises `PluginTaskClosedError` rather than accepting work that would
outlive the generation being torn down — catch it in a `shutdown()` that still
wants to schedule something, or simply do the work inline there. See
[Plugin System › Background tasks](../core-modules/plugins.md#background-tasks).

---

## 8. Custom CLI Commands

Plugins can extend the `baselith` CLI by providing a `cli.py` file in their root directory. The framework automatically scans and registers these commands at startup.

### Implementation

Create a `cli.py` file that implements the `register_parser` function:

```python title="plugins/my-plugin/cli.py"
import argparse

def cmd_my_feature(args):
    print(f"Executing my-feature for {args.name}")
    return 0

def register_parser(subparsers, formatter_class):
    """
    Register custom commands into the main Baselith CLI.
    """
    my_parser = subparsers.add_parser(
        "my-feature",
        help="Custom feature provided by MyPlugin",
        formatter_class=formatter_class
    )
    my_parser.add_argument("name", help="Name to process")
    my_parser.set_defaults(func=cmd_my_feature)
    return my_parser
```

### Usage

Once the file exists, your command becomes available globally — the scan picks up
every `plugins/<dir>/cli.py`, whether or not the plugin is enabled:

```bash
baselith my-feature "Test Name"
```

!!! tip "Professional Output"
    Use the `core.cli.ui` components (like `console`, `print_success`, `Table`) within your custom commands to maintain the premium look and feel of the framework.

### Test via API

```bash
# Test plugin endpoint
curl http://localhost:8000/api/my-plugin/status

# Test via chat orchestration
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "keyword1 something"}'
```

### Test via Web UI

1. Navigate to `http://localhost:8000`
2. Send a message containing one of your patterns (e.g., "keyword1 test")
3. Verify the plugin handler is invoked

!!! tip "Debug Mode"
    Enable debug logging to see intent classification (`CORE_LOG_LEVEL` sets the core
    logger level, `LOG_LEVEL_CONSOLE` the console handler; both default to `INFO`):
    ```bash
    export CORE_LOG_LEVEL=DEBUG
    export LOG_LEVEL_CONSOLE=DEBUG
    baselith run
    ```

---

## 9. Add Unit Tests

Keep tests inside the plugin, as `plugins/example-plugin/tests/` does, and load the
plugin through the real `PluginLoader` — a hyphenated directory such as
`plugins/my-plugin/` is not importable as `plugins.my_plugin` until the loader has
registered it:

```python title="plugins/my-plugin/tests/test_plugin.py"
from pathlib import Path

import pytest
from fastapi import FastAPI

from core.plugins import PluginLoader, PluginRegistry

PLUGIN_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
async def plugin():
    """Load the plugin exactly as the runtime does."""
    loader = PluginLoader(PLUGIN_DIR.parent, PluginRegistry())
    plugin = await loader.load_plugin(PLUGIN_DIR, config={"api_timeout": 5})
    assert plugin is not None
    yield plugin
    await plugin.shutdown()


async def test_config_is_applied(plugin):
    assert plugin.is_initialized()
    assert plugin.get_config("api_timeout") == 5


async def test_agent_executes(plugin):
    agent = plugin.create_agent(service=None)
    await agent.startup()  # LifecycleMixin: state becomes READY
    result = await agent.execute("test query", {})
    assert isinstance(result, str)


async def test_routes_are_mounted_under_plugin_prefix(plugin):
    app = FastAPI()
    for router in plugin.get_routers():
        app.include_router(router, prefix=plugin.get_router_prefix())
    assert "/api/my-plugin/status" in app.openapi()["paths"]
```

The async fixture relies on `asyncio_mode = auto` from the repository `pytest.ini`.

### Run Tests

```bash
# Run the plugin's tests
python -m pytest plugins/my-plugin/tests -v

# Run with coverage
python -m pytest plugins/my-plugin/tests --cov=plugins/my-plugin
```

---

## 10. Maintenance and Lifecycle

Manage your local plugins using several CLI utilities:

### Validation

Before deploying or testing, validate that your plugin conforms to the framework interfaces:

```bash
baselith plugin validate my-plugin
```

### Signing

A plugin that declares `integrity_sha256` in its manifest is verified against
its own tree before the loader executes it, so the hash has to be recomputed
whenever the executable surface changes:

```bash
baselith plugin sign plugins/my-plugin
```

The hash covers what the plugin ships **and** executes — Python sources, the
packaging files `pip install` trusts, `SKILL.md` bodies, native extensions and
shell scripts, and the front-end assets the console serves. Build directories
are excluded, because their contents are neither shipped nor reproducible
between machines: `node_modules` for the JavaScript toolchain, `target` for
Cargo's. Without that, the signature of a plugin with a Rust component depended
on whether `cargo build` had been run locally, and the same tree hashed
differently before and after compiling. The full surface is listed under
[Plugin integrity](../core-modules/plugins.md).

Since hash surface **V5** the digest also covers a canonical projection of the
manifest itself — `permissions`, `python_dependencies`, `min_core_version`,
`name`, `entry_point`. Editing any of those is a re-signing event, exactly like
rebuilding the UI. The three supply-chain keys `integrity_sha256`,
`signature_ed25519` and `hash_surface_version` are excluded from the projection,
so the signing tools can still write them back without invalidating the hash.

In a checkout with `pre-commit install` done, a change to a hashed path *or* to a
manifest re-signs the affected plugin automatically and stages the manifest with
it; without the hook the CI gate `check_plugin_integrity.py` is what catches the
drift. To re-sign on demand:

```bash
python scripts/sign_changed_plugins.py plugins/my-plugin   # one tree
python scripts/sign_changed_plugins.py --all               # every plugin
```

### Disabling/Enabling

Temporarily deactivate a plugin without deleting its files:

```bash
baselith plugin disable my-plugin
baselith plugin enable my-plugin
```

### Deletion

Permanently remove a plugin from the local development environment:

```bash
baselith plugin delete my-plugin
```

### Migrating Legacy Plugins

If you have plugins created before the introduction of the Hybrid Manifest system (where metadata was defined via a python `@property`), you must migrate them.

**Option A: CLI Export (Recommended)**

Use the built-in manifest exporter to generate a `manifest.json` from your existing Python metadata. This preserves compatibility with the current CLI scaffold:

```bash
baselith plugin export-manifest my-legacy-plugin
```

**Option B: Migration Script**

Alternatively, use the migration utility script:

```bash
python scripts/migrate_plugins.py plugins/my-legacy-plugin
```

This will automatically extract the metadata from your Python file, generate a `manifest.yaml` (preferred), and remove the obsolete `metadata` method from your code.

---

## 11. Backstage Registration (Automatic)

Nothing to author: the framework's **Backstage Entity Provider** exports every
registered plugin to the portal automatically. The catalog Component (plus its
API entity, owner Group, and Resource dependencies) is generated live from
your `manifest.yaml` and the plugin registry, so keep the manifest accurate:

* `name`, `description`, `author` → entity identity and `spec.owner`
* `readiness` → `spec.lifecycle` (`stable` → `production`, `deprecated` →
  `deprecated`, anything else → `experimental`)
* `tags`, `category` → catalog tags/labels (also drive pattern detection)
* `required_resources` → `spec.dependsOn` Resource entities;
  `optional_resources` → annotation only
* `plugin_dependencies` → `spec.dependsOn` Component references
* routers → a per-plugin `API` entity with an inline, route-scoped OpenAPI
  definition
* ship an `mkdocs.yml` in the plugin directory to light up the TechDocs tab
  (the annotation is omitted otherwise)

Do **not** add a static `catalog-info.yaml` or a portal `catalog.locations`
entry for your plugin — the provider already emits the entity, and duplicate
locations conflict in the Backstage catalog. See
[Backstage Integration](backstage.md) for the full mapping table.

---

## Next Steps

After creating your plugin:

* **Document**: Add comprehensive `README.md` to your plugin directory
* **Distribute**: See [Packaging Guide](packaging.md) to prepare for distribution
* **Extend**: Add [Frontend Integration](frontend-integration.md) for custom UI
* **Publish**: Submit to the official [Plugin Marketplace](marketplace.md) using the command:

    ```bash
    baselith plugin marketplace publish .
    ```

    !!! note "Fixed Endpoint"
        For security, the `publish` command always targets the official BaselithCore Marketplace Hub, regardless of local environment overrides.

---

## Troubleshooting

??? failure "Plugin not loading"
    **Symptom**: Plugin doesn't appear in `plugin list`

    **Diagnosis**:
    ```bash
    baselith plugin status --name my-plugin
    ```

    **Common causes**:
    - Plugin disabled in `configs/plugins.yaml`
    - Syntax error in `plugin.py`
    - Missing required dependencies

    **Solution**: Check logs and fix errors shown

??? failure "Intent not triggering"
    **Symptom**: Messages with patterns don't invoke plugin handler

    **Diagnosis**: Check orchestration logs with `DEBUG` logging

    **Common causes**:
    - Lower priority than competing intents
    - Patterns too generic (e.g., just "help")
    - Handler registration issue

    **Solution**: Increase priority or make patterns more specific

??? failure "API endpoints returning 404"
    **Symptom**: `/api/my-plugin/*` returns 404

    **Common causes**:
    - `create_router()` not implemented on the plugin class
    - Router not returning `APIRouter` instance
    - Plugin not implementing `RouterPlugin` mixin
    - A `prefix=` on the `APIRouter` doubling the path
      (`/api/my-plugin/my-plugin/...`)

    **Solution**: Verify plugin inherits `RouterPlugin`, returns the router, and
    leaves the router prefix empty

??? failure "New SKILL.md not visible to the catalog"
    **Symptom**: A freshly added `skills/<name>/SKILL.md` is not returned by
    the skill catalog or activation

    **How lookup works**: the first lookup of a **never-before-seen** name
    forces a catalog re-walk, so a new skill is normally visible immediately.
    A name that already missed once is **negative-cached** until the next
    refresh (at most one catalog TTL) — repeated lookups of unknown names do
    not re-walk the catalog.

    **Solution**: use the skill's final name from the start, or wait one
    catalog TTL / trigger a refresh after writing the file

??? failure "Bundled skill file fails activation with `SkillSandboxError`"
    **Symptom**: activating a skill that ships `scripts/`, `references/` or
    `assets/` raises `SkillSandboxError`

    **Cause**: every bundled file is enumerated and sandbox-validated at
    activation time; a symlink resolving outside the loader's skill roots
    fails the whole activation (a security signal, not a formatting
    accident). The same containment applies when `run_skill_script`
    executes a bundled `.py` helper — absolute paths, `..` traversal and
    symlink escapes are rejected.

    **Solution**: ship real files (no symlinks out of the plugin tree) and
    reference scripts by their path relative to the skill's `scripts/`
    directory. See
    [Declarative Skills › Bundled files](../core-modules/skills.md#bundled-files-scripts-references-assets)
