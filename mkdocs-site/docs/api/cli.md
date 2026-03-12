---
title: CLI Commands
description: Command line interface commands
---



The framework's **Command Line Interface (CLI)** provides tools to manage the system lifecycle, plugins, work queues, cache, and more. It facilitates development, debugging, and administration operations without directly interacting with the REST API.

---

## Command Menu

The CLI provides a categorized menu powered by Rich for enhanced developer experience.

```bash
baselith --help
baselith --format json <command>  # Global output formatting
```

**Output**:

```text
██████╗  █████╗ ███████╗███████╗██╗     ██╗████████╗██╗  ██╗ ██████╗  ██████╗ ██████╗ ███████╗      
 ██╔══██╗██╔══██╗██╔════╝██╔════╝██║     ██║╚══██╔══╝██║  ██║██╔════╝ ██╔═══██╗██╔══██╗██╔════╝      
 ██████╔╝███████║███████╗█████╗  ██║     ██║   ██║   ███████║██║      ██║   ██║██████╔╝█████╗        
 ██╔══██╗██╔══██║╚════██║██╔══╝  ██║     ██║   ██║   ██╔══██║██║      ██║   ██║██╔══██╗██╔══╝        
 ██████╔╝██║  ██║███████║███████╗███████╗██║   ██║   ██║  ██║╚██████╗ ╚██████╔╝██║  ██║███████╗ ██╗  
 ╚═════╝ ╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝╚═╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝ ╚═╝
  Multi-Agent, Plugin-First Framework  •  v0.2.0

╭───────────────────────────────────────────── Command Menu ─────────────────────────────────────────────╮
│                                                                                                        │
│   SCAFFOLDING                   init            Bootstrap a new project                                │
│                                 plugin          Manage framework plugins                               │
│                                                                                                        │
│   DEVELOPMENT                   run             Start the development server                           │
│                                 shell           Start interactive shell                                │
│                                 docs            Generate documentation                                 │
│                                                                                                        │
│   SYSTEM & HEALTH               doctor          Run system diagnostics                                 │
│                                 verify          Verify environment configuration                       │
│                                 info            View system dashboard                                  │
│                                 config          Manage configuration                                   │
│                                                                                                        │
│   INFRASTRUCTURE                db              Manage database systems                                │
│                                 cache           Manage Redis cache                                     │
│                                 queue           Manage task queues                                     │
│                                                                                                        │
│   QUALITY & TESTS               test            Run test suite                                         │
│                                 lint            Run code linters                                       │
│                                                                                                        │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────╯

──────────────────────────── Quick Start ─────────────────────────────
  Bootstrap a new project         baselith init my-app
  Check system health             baselith doctor
  Start dev server                baselith run
  Run the test suite              baselith test
```

---

## Global Options

The framework supports global flags that modify the behavior of all commands.

| Flag        | Description                                                                                            |
| ----------- | ------------------------------------------------------------------------------------------------------ |
| `--format`  | Set the output format: `text` (default, beautiful Rich output) or `json` (machine-readable for CI/CD). |
| `--version` | Show the framework version.                                                                            |

!!! tip "JSON for CI/CD"
    When using `--format json`, all logical output is emitted as a single JSON object to `stdout`. This is the professional standard for automation and pipeline integration.

---

## General

### `doctor` - System Diagnostics

Verify system health, checking connections to external services and configuration.

```bash
baselith doctor
```

**Options**:

| Flag            | Description                                           |
| --------------- | ----------------------------------------------------- |
| `--format json` | Emit machine-readable JSON output for CI/CD pipelines |

**Example Output**:

```text
╭─────────────────────────╮
│ 🩺 Baselith-Core Doctor │
│   System Diagnostics    │
╰─────────────────────────╯

┏━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃  Status  ┃ Component     ┃ Message                      ┃ Details/Resolution ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ ✅ PASS  │ Environment   │ Found config at .env         │                    │
│ ✅ PASS  │ LLM Provider  │ Ollama connected             │                    │
│ ✅ PASS  │ Redis (Cache) │ Connected (localhost:6379)   │                    │
│ ✅ PASS  │ Qdrant        │ Connected (localhost:6333)   │                    │
│ ✅ PASS  │ GraphDB       │ Connected (localhost:6379)   │                    │
│ ✅ PASS  │ Plugins       │ 5 plugin(s) found            │                    │
└──────────┴───────────────┴──────────────────────────────┴────────────────────┘

Results: 6 passed
✅ System ready! Run: baselith run

⏱  Completed in 0.07s
```

**JSON Output** (`baselith --format json doctor`):

```json
{
  "passed": 4,
  "warnings": 1,
  "failed": 0,
  "checks": [...],
  "elapsed_seconds": 1.23
}
```

**When to use**:

- At startup to verify all dependencies are ready
- After configuration changes
- For troubleshooting connectivity issues
- In CI/CD pipelines with `--json` for automated health gates

---

### `info` - System Dashboard

Show a high-level overview of the system architecture, versions, and active plugins.

```bash
baselith info
baselith --format json info   # Machine-readable JSON for CI
```

**Options**:

| Flag            | Description                                           |
| --------------- | ----------------------------------------------------- |
| `--format json` | Emit machine-readable JSON output for CI/CD pipelines |

**Example Output**:

```text
╭────── Framework ───────╮╭── Current Workspace ───╮
│   Version   0.2.0      ││   Name       app      │
│   Python    3.10.12    ││   In Project ✅ Yes   │
│   OS        Linux      ││   Plugins    2        │
╰────────────────────────╯╰────────────────────────╯

⏱  Completed in 42ms
```

---

### `verify` - Installation Check

Perform a rigorous check of the installation, including file structure, python version, and core module availability.

```bash
baselith verify
baselith --format json verify   # Machine-readable JSON for CI
```

**Options**:

| Flag            | Description                                           |
| --------------- | ----------------------------------------------------- |
| `--format json` | Emit machine-readable JSON output for CI/CD pipelines |

---

## Plugin

### `plugin list` / `plugin status` - List & Status

Show all available plugins with health, readiness, and config alignment.

```bash
baselith plugin list
baselith plugin status [--name <name>]
```

**Enhanced columns**: Status, Plugin Name, Version, Type, Readiness (stable/beta/alpha), Config alignment (✓/⚠/—), Components.

**Example Output**:

```text
                                Local Plugin Status
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Status       ┃ Plugin Name    ┃ Version ┃ Type   ┃ Readiness ┃ Config ┃ Components      ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ ✅ Active    │ auth           │ 0.2.0   │ Agent  │ stable    │   ✓    │ Agent, Router   │
│ ⏸️ Disabled  │ test-feature   │ 0.2.0   │ Agent  │ beta      │   ⚠    │ Agent           │
│ ❌ Broken    │ legacy-module  │ ?       │ Unknown│ stable    │   —    │ None            │
└──────────────┴────────────────┴─────────┴────────┴───────────┴────────┴─────────────────┘
Config column: ✓ = aligned  ⚠ = mismatch  — = not in plugins.yaml
```

---

### `plugin create` - Create New Plugin

Generate scaffolding for a new plugin with the correct structure.

```bash
baselith plugin create <name> --type [agent|router|graph]
baselith plugin create --interactive  # Interactive wizard
```

**Parameters**:

- `<name>`: Plugin name (e.g. `finance-assistant`)
- `--type`: Plugin type (`agent`, `router`, `graph`)
- `-i, --interactive`: Launch the interactive creation wizard

**Interactive Wizard** prompts for:

- Plugin name, type, description, author, tags
- Environment variables
- Auto-registration in `configs/plugins.yaml`

---

### `plugin validate` - Validate Plugin

Comprehensive validation of a local plugin's syntax, structure, manifest, and dependencies.

```bash
baselith plugin validate <name>
baselith --format json plugin validate <name>
```

**Checks performed**:

| Check           | Description                                       |
| --------------- | ------------------------------------------------- |
| Python Syntax   | AST parsing for correctness                       |
| Plugin Class    | Inheritance from framework interfaces             |
| Manifest Schema | Required fields: `name`, `version`, `description` |
| Env Variables   | Environment variable presence check               |
| Python Deps     | Package importability verification                |
| Plugin Deps     | Sibling plugin existence check                    |

---

### `plugin deps` - Dependency Management

Verify and install plugin dependencies.

```bash
baselith plugin deps check <name>     # Check all dependencies
baselith plugin deps install <name>   # Install missing Python packages
baselith plugin deps install <name> -y  # Skip confirmation
```

**`deps check`** verifies: Python packages, sibling plugins, environment variables, and required resources.

**Example Output**:

```text
                    Dependencies: my-plugin
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Category         ┃ Dependency       ┃   Status   ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Python Package   │ requests         │  ✅ OK     │
│ Plugin           │ auth             │  ✅ OK     │
│ Environment Var  │ API_KEY          │  ❌ Missing│
└──────────────────┴──────────────────┴────────────┘
```

---

### `plugin config` - Configuration Management

Manage `configs/plugins.yaml` directly from the CLI.

```bash
baselith plugin config show [name]         # Show all or specific plugin config
baselith plugin config set <name> <key> <value>  # Set a value (auto-coerces types)
baselith plugin config get <name> <key>    # Get specific value
baselith plugin config reset <name>        # Reset to defaults
```

**Type coercion**: Values like `true`/`false` are auto-converted to booleans, numbers to int/float.

---

### `plugin logs` - View Plugin Logs

Display filtered runtime logs from the `logs/` directory.

```bash
baselith plugin logs <name> [-n 50] [-l ERROR]
baselith --format json plugin logs <name>
```

**Options**:

| Flag          | Description                                              |
| ------------- | -------------------------------------------------------- |
| `-n, --lines` | Max lines to display (default: 50)                       |
| `-l, --level` | Minimum log level: DEBUG, INFO, WARNING, ERROR, CRITICAL |

Supports both JSON structured logs and standard text format.

---

### `plugin tree` - Dependency Tree

Visualize the inter-plugin dependency graph.

```bash
baselith plugin tree             # Full ecosystem tree
baselith plugin tree <name>      # Single plugin tree
baselith --format json plugin tree
```

**Example Output**:

```text
🏗️  Baselith Plugin Ecosystem
├── ✅ auth v0.2.0  [security, core]
├── 📦 langchain v0.2.0
├── ✅ rag v0.2.0  [ai, retrieval]
│   ├── ✅ auth v0.2.0
│   └── 📦 langchain v0.2.0
└── 🧪 experimental v0.0.1  [alpha]
    └── ❌ missing-plugin (missing)
```

---

### `plugin disable` / `plugin enable` - Toggle Plugins

Disable or enable plugins with individual or bulk operations.

```bash
baselith plugin disable <name>      # Disable single plugin
baselith plugin enable <name>       # Enable single plugin
baselith plugin disable --all       # Bulk disable all
baselith plugin enable --all        # Bulk enable all
```

Both commands auto-sync state with `configs/plugins.yaml`.

---

### `plugin delete` - Delete Plugin

Definitively remove a local plugin directory from the filesystem.

```bash
baselith plugin delete <name> [--force]
```

**Options**:

- `--force`: Skip the confirmation prompt.

### `plugin export-manifest` - Export Metadata

Generate a `manifest.json` file from a legacy plugin's Python metadata definition.

```bash
baselith plugin export-manifest <name>
```

---

### `plugin info` - Local Plugin Details

Examine detailed metadata for a local plugin.

```bash
baselith plugin info <name>
baselith --format json plugin info <name>
```

---

### `plugin marketplace search` - Search Marketplace

Search for plugins available in the Baselith Marketplace.

```bash
baselith plugin marketplace search <query>
```

---

### `plugin marketplace info` - Marketplace Plugin Details

Get detailed metadata for a specific marketplace plugin.

```bash
baselith plugin marketplace info <plugin_id>
```

---

### `plugin marketplace install` - Install Plugin

Install a plugin directly from the marketplace, including its dependencies.

```bash
baselith plugin marketplace install <plugin_id> [--version <v>] [--force]
```

**Options**:

- `--version`: Install a specific version instead of the latest
- `--force`: Force reinstallation even if already installed

---

### `plugin marketplace uninstall` - Uninstall Plugin

Remove an installed plugin from the system.

```bash
baselith plugin marketplace uninstall <plugin_id>
```

---

### `plugin marketplace update` - Update Plugin

Check for and install updates for a specific plugin.

```bash
baselith plugin marketplace update <plugin_id>
```

---

## Project

### `init` - Initialize Project

Create a new project based on the framework with pre-defined templates.

```bash
baselith init [name] [--template <template>]
```

**Interactive Mode**:
If you run `baselith init` without arguments, the CLI will enter an **Interactive Scaffolding Wizard** powered by `Rich.prompt`. It will guide you through project naming and template selection with real-time validation.

**Available Templates**:

- `minimal`: Minimal project with core only
- `full`: Full project with all services configured
- `chat-only`: Chat service only, minimal footprint
- `rag-system`: RAG system with vector store and memory
- `baselith-core`: BaselithCore system with swarm and A2A

**Example**:

```bash
baselith init my-assistant --template rag-system
```

---

## System

### `run` - Start Server

```bash
baselith run --host 0.0.0.0 --port 8000 --reload --workers 1 --log-level info
```

**Options**:

| Flag          | Description                                               |
| ------------- | --------------------------------------------------------- |
| `--host`      | Network interface to bind the server to                   |
| `--port`      | Network port to listen on                                 |
| `--reload`    | Enable hot-reloading for rapid development                |
| `--no-reload` | Disable hot-reloading (production-like behavior)          |
| `--workers`   | Number of parallel worker processes (ignored with reload) |
| `--log-level` | Set the verbosity of system logs (info, debug, etc.)      |

### `test` - Run Tests

Execute the pytest suite with coverage reporting in a structured output. Displays execution timing on completion.

```bash
baselith test [path] [--no-cov] [-v] [-m MARKERS] [-x] [--parallel] [--format json]
```

**Options**:

| Flag            | Description                                            |
| --------------- | ------------------------------------------------------ |
| `path`          | Specific test file or directory to execute             |
| `--no-cov`      | Omit code coverage analysis for faster execution       |
| `-v`            | Provide detailed output for each test case             |
| `-m`            | Filter tests by pytest markers                         |
| `-x`            | Terminate immediately upon the first test failure      |
| `--parallel`    | Harness multiple CPU cores for parallel test execution |
| `--format json` | Output test status and execution metadata as JSON      |

### `lint` - Lint Code

Run `ruff` and `mypy` across the codebase. Displays execution timing on completion.

```bash
baselith lint [--fix] [--no-mypy]
```

**Options**:

| Flag        | Description                                             |
| ----------- | ------------------------------------------------------- |
| `--fix`     | Automatically resolve formatting and linting violations |
| `--no-mypy` | Bypass static type checking with MyPy                   |

---

### `shell` - Interactive REPL

Start an interactive Python shell pre-loaded with the Baselith-Core context (e.g., configurations, vector stores, LLM instances). If IPython is installed, it is used by default.

```bash
baselith shell
```

**Features**:

- Auto-loads `settings`, `LLMService`, and `QdrantStore`
- Ideal for quick testing of connections and logic

---

## Database

### `db status` - Database Status

Show the connection status of all persistent data stores (Qdrant, Redis, GraphDB).

```bash
baselith db status
baselith --format json db status
```

---

### `db reset` - Clear Databases

Wipe all collections within VectorStores and flush the Cache completely.

```bash
baselith db reset
```

!!! danger "Warning"
    This operation is irreversible. You will lose all embeddings and cached configurations.

---

## Config

### `config show` - Show Configuration

Displays the current active configuration across all system layers (Core, LLM, Chat, VectorStore) in a beautiful split-layout dashboard.

```bash
baselith config show
```

**Example Output**:

```text
╭──────────────────────────╮
│ 📋 Current Configuration │
╰──────────────────────────╯
╭────── Core Settings ───────╮╭────── LLM Settings ───────╮
│                            ││                           │
│ Log Level      info        ││ Provider     ollama       │
│ Debug          False       ││ Model        llama3.2     │
│ Plugin Dir     plugins     ││ Cache Enable True         │
│ Data Dir       data        ││                           │
╰────────────────────────────╯╰───────────────────────────╯
╭────── Chat Settings ───────╮╭─── VectorStore Settings ──╮
│                            ││                           │
│ Streaming      True        ││ Provider     qdrant       │
│ Initial Search 10          ││ Host         localhost    │
│ Final Top K    3           ││ Collection   agents       │
╰────────────────────────────╯╰───────────────────────────╯
```

### `config validate` - Validate Settings

Validates that all current configuration settings are valid and that services are accessible.

```bash
baselith config validate
```

---

## Cache

### `cache stats` - Cache Statistics

Show Redis cache usage statistics.

```bash
baselith cache stats
```

**Example Output**:

```text
╭─────────────────────────────────────────────────────────────╮
│                                                             │
│                    📦 Cache Statistics                      │
│                Redis Database Memory Info                   │
│                                                             │
╰─────────────────────────────────────────────────────────────╯

┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Metric               ┃ Value    ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ Total Keys           │ 1247     │
│ Used Memory (Human)  │ 12.4M    │
│ Peak Memory (Human)  │ 14.2M    │
│ Memory Fragmentation │ 1.05     │
└──────────────────────┴──────────┘
```

---

### `cache clear` - Clear Cache

Delete all keys from cache (useful for debugging).

```bash
baselith cache clear
```

!!! danger "Warning"
    This operation is irreversible and may cause temporary performance degradation.

---

## Error Handling & Reliability

### Global Exception Interceptor

BaselithCore features a professional-grade global exception handler. In the event of an unexpected crash, the CLI will not pollute your terminal with raw tracebacks. Instead:

1. A clean, user-friendly error message is displayed.
2. A detailed **Crash Report** containing the full traceback and environment metadata is automatically saved to:
    `~/.baselith/crash-report.log`

This allows for easier debugging by developers without overwhelming end-users.

---

## Queue

### `queue status` - Queue Status

Show task queue status (RQ).

```bash
baselith queue status
```

**Example Output**:

```text
╭─────────────────────────────────────────────────────────────╮
│                                                             │
│                      📋 Queue Status                        │
│               Background Task Orchestration                 │
│                                                             │
╰─────────────────────────────────────────────────────────────╯

┏━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Metric         ┃ Value ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ Active Workers │ 2     │
│ Pending Jobs   │ 12    │
│ Running Jobs   │ 3     │
│ Completed Jobs │ 1847  │
│ Failed Jobs    │ 0     │
└────────────────┴───────┘

Worker Details:
1. baselith-worker-1 - idle
2. baselith-worker-2 - busy
```

---

### `queue worker` - Start Worker

Start a worker to process tasks from the queue.

```bash
baselith queue worker --concurrency 4
```

**Parameters**:

- `--concurrency`: Number of parallel tasks (default: CPU cores)

!!! tip "Production"
    In production, use a process manager like `supervisor` or `systemd` to manage workers.

---

## Docs

### `docs generate` - Generate Documentation

Generate OpenAPI documentation for all registered REST endpoints.

```bash
baselith docs generate
```

**Output**:

```text
✅ Scanning endpoints...
✅ Found 47 endpoints across 8 plugins
✅ Generated: mkdocs-site/docs/api/specs/openapi.json
✅ Generated: mkdocs-site/docs/api/specs/openapi.yaml
✅ Generated: mkdocs-site/docs/api/specs/postman_collection.json
```

---

## Common Workflows

### Initial Setup

```bash
# 1. Verify environment
baselith doctor

# 2. List available plugins
baselith plugin list

# 3. Generate docs
baselith docs generate
```

---

### Plugin Development

```bash
# 1. Create plugin
baselith plugin create my-plugin --type agent

# 2. Check local plugin info
baselith plugin info my-plugin

# 3. Develop...
```

---

### Troubleshooting

```bash
# Verify health
baselith doctor

# Check problematic plugin status
baselith plugin status --name auth

# Clear cache if anomalies
baselith cache clear

# Check queue for stuck tasks
baselith queue status
```

---

## Tips & Best Practices

!!! tip "Bash Alias"
    Create an alias for speed:
    ```bash
    alias cli="baselith"
    cli doctor
    ```

!!! tip "Logging"
    Use `--verbose` for detailed output:
    ```bash
    baselith --verbose plugin create my-plugin
    ```

!!! warning "Production vs Development"
    Some commands (e.g. `plugin reload`) only work in `DEBUG=true` mode.
