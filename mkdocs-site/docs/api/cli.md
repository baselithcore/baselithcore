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
  Multi-Agent, Plugin-First Framework  •  v0.35.0  •  https://baselithcore.xyz

╭──────────────────────────────────────── Command Menu ────────────────────────────────────────╮
│   SCAFFOLDING               init            Bootstrap a new project                          │
│                             setup           Prepare a BaselithCore environment               │
│                             plugin          Manage framework plugins                         │
│   DEVELOPMENT               run             Start the development server                     │
│                             up              Download and start the complete Docker runtime   │
│                             shell           Start interactive shell                          │
│                             docs            Generate documentation                           │
│   SYSTEM & HEALTH           doctor          Run system diagnostics                           │
│                             verify          Verify environment configuration                 │
│                             info            View system dashboard                            │
│                             config          Manage configuration                             │
│   INFRASTRUCTURE            db              Manage database systems                          │
│                             cache           Manage Redis cache                               │
│                             queue           Manage task queues                               │
│   QUALITY & TESTS           test            Run test suite                                   │
│                             lint            Run code linters                                 │
╰──────────────────────────────────────────────────────────────────────────────────────────────╯

Usage: baselith <command>
Use baselith <command> --help for detailed info on any command.

──────────────────────────── Quick Start ─────────────────────────────
  Bootstrap a new project           baselith init my-app
  Check system health               baselith doctor
  Start dev server                  baselith run
  Run the test suite                baselith test
```

---

## Global Options

The framework supports global flags that modify the behavior of all commands.

| Flag        | Description                                                                                            |
| ----------- | ------------------------------------------------------------------------------------------------------ |
| `--format`  | Set the output format: `text` (default, beautiful Rich output) or `json` (machine-readable for CI/CD). Accepted before or after any (sub)command. |
| `--version` | Show the framework version.                                                                            |
| `--help`, `-h` | Show the categorized command menu.                                                                  |

!!! tip "JSON for CI/CD"
    When using `--format json`, all logical output is emitted as a single JSON object to `stdout`. This is the professional standard for automation and pipeline integration. `--format` is the only output-shaping global flag — there is no global `--verbose` flag.

---

## General

### `doctor` - System Diagnostics

Verify system health, checking connections to external services and configuration.

```bash
baselith doctor
```

**Options**:

| Flag            | Description                                                          |
| --------------- | -------------------------------------------------------------------- |
| `--json`        | Emit machine-readable JSON output for CI/CD pipelines                |
| `--format json` | Same output, through the global formatting flag                      |
| `--fix`         | Apply the safe local repairs: create `.env` and the data directories |
| `--core-only`   | Skip the three plugin checks and validate only the core runtime      |

`--fix` is deliberately narrow: it creates `.env` from `.env.example` (falling
back to `configs/.env.base`) and creates the missing data directories. It never
starts a service, never installs a dependency and never recovers a credential
from a running container.

A failed check carries the command that fixes it in its Details column. Those
hints name the backing store they need — for the bundled stack that is
`docker compose up -d postgres redis qdrant` from `compose.yaml`, the Compose v2
command (`docker-compose`, the v1 Python wrapper, is deprecated and the hints no
longer suggest it). The environment check is the one exception: it accepts
configuration from the environment as readily as from a `.env` file, because a
container deployment injects a ConfigMap and Secret through `envFrom` and has
deliberately no file on disk.

**Checks**: project runtime (Python, Docker, core dependencies, `.env`, data
directories), infrastructure (LLM provider, Redis, Qdrant, PostgreSQL, GraphDB),
runtime configuration (telemetry, migrations mode) and plugin readiness
(plugins, plugin dependencies, plugin frontends). The last three are the ones
`--core-only` skips.

**Example Output** (a host with Qdrant and PostgreSQL down, dependency list
elided):

```text
╭─────────────────────────╮
│ 🩺 Baselith-Core Doctor │
│   System Diagnostics    │
╰─────────────────────────╯

┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  Status  ┃ Component           ┃ Message                        ┃ Details/Resolution             ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ ✅ PASS  │ Python              │ Python 3.12.13                 │ /opt/homebrew/…/bin/python     │
│ ❌ FAIL  │ Environment         │ .env file not found            │ Run: cp .env.example .env, or  │
│          │                     │                                │ use: baselith doctor --fix     │
│ ❌ FAIL  │ Data Directories    │ 2 data director(y/ies) missing │ Run: baselith doctor --fix     │
│ ✅ PASS  │ Docker              │ Docker daemon reachable        │                                │
│ ✅ PASS  │ Core Dependencies   │ Common local extras installed  │                                │
│ ✅ PASS  │ LLM Provider        │ Ollama connected               │                                │
│          │                     │ (localhost:11434)              │                                │
│ ✅ PASS  │ Redis (Cache)       │ Connected (localhost:6379)     │                                │
│ ❌ FAIL  │ Qdrant              │ Cannot connect                 │ Run: docker compose up -d      │
│          │                     │ (localhost:6333)               │ qdrant                         │
│ ❌ FAIL  │ PostgreSQL          │ Cannot connect (postgres:5432) │ Run: docker compose up -d      │
│          │                     │                                │ postgres                       │
│ ✅ PASS  │ GraphDB             │ Connected (localhost:6379)     │                                │
│ ✅ PASS  │ Telemetry           │ Disabled                       │                                │
│ ✅ PASS  │ DB Migrations       │ Run during application startup │ For predictable startup,       │
│          │                     │                                │ prefer false and run: baselith │
│          │                     │                                │ db migrate                     │
│ ✅ PASS  │ Plugins             │ 10 plugin(s) found             │                                │
│ ❌ FAIL  │ Plugin Dependencies │ 15 missing plugin dependency   │ Run: baselith plugin deps      │
│          │                     │ declaration(s)                 │ install <plugin>. Missing: …   │
│ ✅ PASS  │ Plugin Frontends    │ Declared frontend builds       │                                │
│          │                     │ present                        │                                │
└──────────┴─────────────────────┴────────────────────────────────┴────────────────────────────────┘

Results: 10 passed, 5 failed

⚠️  Some critical checks failed. Fix them before running the server.

⏱  Completed in 508ms
```

**JSON Output** (`baselith doctor --json`, or `baselith --format json doctor`):

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

### `setup` - Environment Bootstrap

Prepare a local environment from a profile, without starting the server.

```bash
baselith setup                 # dev profile (default)
baselith setup docker-core     # Docker runtime profile
```

`setup dev` writes the developer defaults into the root `.env` (copying
`.env.example` when the file is absent), then runs the flags you asked for —
services, migrations — followed by `doctor --fix`, and prints a step table with
one row per stage. It exits `1` when any row needs attention, so it doubles as
the check you run after changing a local service.

`setup docker-core` is the narrower one: it prepares `.env` and
`configs/.env.docker.core`, the env file the Docker Compose runtime reads, and
stops there.

Both preserve a credential that is already valid and generate a `DB_PASSWORD`
and a `SECRET_KEY` only where a placeholder is still in place. Neither downloads
the core image nor builds it — `baselith up` does that — and neither starts a
backing service unless `setup dev` is given `--start-services`.

**Options** (dev profile only; `docker-core` ignores them):

| Flag              | Description                                                  |
| ----------------- | ------------------------------------------------------------ |
| `--install-deps`  | Install missing plugin dependencies instead of a dry run     |
| `--with-plugins`  | Include the local plugin checks in the flow                  |
| `--start-services`| Start PostgreSQL, Redis and Qdrant with Docker Compose       |
| `--migrate`       | Apply database migrations once the services answer           |
| `--wait-timeout`  | Seconds to wait for the services to become ready (default 60)|
| `--json`          | Machine-readable output (`docker-core` profile only)         |

!!! warning "`--json` and the dev profile"
    `baselith setup --json` on the dev profile exits `1` with
    `JSON output is not supported for setup orchestration yet.` The flag is
    implemented for `setup docker-core`.

See the [Docker Core runbook](../getting-started/docker-core.md) for the
`docker-core` profile end to end.

---

### `up` - Docker Runtime

Prepare a standalone Docker runtime project and start BaselithCore with
PostgreSQL, FalkorDB/Redis, and Qdrant.

```bash
baselith up
baselith up --image ghcr.io/baselithcore/baselithcore:<tag>
```

The command creates the missing runtime files, prepares
`configs/.env.docker.core`, persists the selected `BASELITH_CORE_IMAGE`, pulls
the backing service images, builds the API image, starts the stack, and waits
for `/health`.

Persisting the image is important for plugin workflows: later calls to
`baselith plugin add <repo-or-path> --docker` reuse the same base image instead
of falling back to the package version default.

!!! note "Generated env files are owner-only"
    `baselith up` and `baselith setup` generate a `DB_PASSWORD` and a
    `SECRET_KEY` whenever the file still holds a placeholder, and write them as
    plain `KEY=value` lines — Docker Compose's `env_file` and pydantic-settings
    read the file before any of our code runs, so there is nothing that could
    decrypt them. Both writers therefore create `.env` and
    `configs/.env.docker.core` with mode `0600` and re-apply that mode on every
    write, including to a file an older version left `0644`. Keep it that way
    when you edit the file by hand, and never commit it.

**Options**:

| Flag        | Description                                               |
| ----------- | --------------------------------------------------------- |
| `--image`   | Core image used as the API base image                     |
| `--timeout` | Seconds to wait for the Core health check (default: 300) |

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
│   Version   0.35.0     ││   Name       app      │
│   Python    3.12.6     ││   In Project ✅ Yes   │
│   OS        Linux      ││   Plugins    2        │
╰────────────────────────╯╰────────────────────────╯

Completed in 42ms
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

**Enhanced columns**: Status, Plugin Name, Version, Type, Readiness (stable/beta/alpha), Config alignment (✓ / WARN / —), Components.

**Example Output**:

```text
                                Local Plugin Status
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Status       ┃ Plugin Name    ┃ Version ┃ Type   ┃ Readiness ┃ Config ┃ Components      ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ ✅ Active    │ auth           │ 0.29.0  │ Agent  │ stable    │   ✓    │ Agent, Router   │
│ -- Disabled  │ test-feature   │ 0.29.0  │ Agent  │ beta      │  WARN  │ Agent           │
│ ❌ Broken    │ legacy-module  │ ?       │ Unknown│ stable    │   —    │ None            │
└──────────────┴────────────────┴─────────┴────────┴───────────┴────────┴─────────────────┘
Config column: ✓ = aligned   WARN = mismatch   — = not in plugins.yaml
```

---

### `plugin add` - Install into Docker

```bash
baselith setup docker-core
baselith plugin add <repository> --docker
```

`--docker` validates build inputs, prepares dependencies, builds declared frontends
with a temporary Node container, installs Python requirements in the API image,
starts Compose and checks HTTP. It does not check for plugin packages in the host
Python environment. Invalid declared Core bounds, failed builds and failed probes
return a nonzero exit code. Missing Core bounds remain a legacy warning.

Use `--ref <branch-or-tag>` on the initial clone; existing directories are reused.
`--force` replaces only a verifiably clean Git checkout. `--install-deps` without
`--docker` installs Python dependencies in the host environment.

An HTTP 200 is a reachability check, not a complete application test. The current
installer does not implement transactional rollback or fingerprint-based sync.
See the [Docker Core runbook](../getting-started/docker-core.md) for configuration,
local plugin development and retry instructions.

### `plugin sync` - Reconcile the Docker Runtime

Rebuild the Docker core runtime from whatever is enabled under `plugins/` right
now, instead of reinstalling one plugin at a time.

```bash
baselith plugin sync --docker
```

For every enabled plugin the command validates the installation manifest and
the declared core bounds, writes the combined Python requirements, builds the
declared frontends, rebuilds and restarts the `api` service, waits for
`/health`, then probes each plugin that declares `health_endpoint` or a
`frontend` contract. A plugin whose manifest is missing or invalid stops the
sync with a nonzero exit code rather than being skipped.

`--docker` is what selects the Docker runtime; without it the command only
prints the local plugin status, exactly like `plugin status`. The sync has no
fingerprinting — it rebuilds every time and relies on Docker layer reuse for the
cost.

### `plugin create` - Scaffold a Plugin

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

### `plugin schema-init` - Build Plugin Schemas at Deploy Time

Run every enabled plugin's `init_schema()` **as the role that owns the tables**,
before the application starts.

The serving process must not hold DDL. A deployment that isolates tenants at the
database connects as a least-privilege role — `NOSUPERUSER NOBYPASSRLS`, owning
nothing — because PostgreSQL exempts a superuser, a `BYPASSRLS` role and a
table's *owner* from that table's own row-level-security policy. A plugin that
builds its schema from the serving process cannot run there: it fails with
`permission denied for schema public`, or, once granted that, with
`must be owner of table …` — and ownership is not a privilege that can be
granted. Worse, a plugin that *did* own its tables would be exempt from their
policies.

```bash
baselith plugin schema-init                # every enabled plugin
baselith plugin schema-init --plugin my-plugin   # just one
baselith plugin schema-init --format json  # machine-readable summary
```

Plugins are loaded **cold** — instantiated, never initialised — so no runtime
client is opened. A plugin with no schema of its own implements nothing and is
reported as such. The exit code is the number of plugins that failed, so a
deploy job stops instead of starting an application against a half-built
schema.

In Kubernetes the chart runs this for you: set
`database.pluginSchemaInit.enabled` and the Job lands after the migrations and
after the runtime role exists. See
[Multi-Tenancy](../advanced/multi-tenancy.md#defense-in-depth-row-level-security).

---

### `plugin sign` - Sign Plugin Integrity

Compute the SHA-256 hash of everything a plugin ships and runs and write it into
the manifest's `integrity_sha256` field. The loader verifies this hash before
executing plugin code (and rejects unsigned plugins when
`BASELITH_REQUIRE_SIGNED_PLUGINS=true`).

Since 0.27 the surface also covers native modules, shell scripts and the
front-end assets served from the plugin's origin (`ui/dist/**`, `static/**`), so
re-run `sign` after `npm run build` — see
[Packaging › What is hashed](../plugins/packaging.md#what-is-hashed).

```bash
baselith plugin sign <path>            # Compute and write into the manifest
baselith plugin sign <path> --check    # Compute and print the hash only
```

**Options**:

- `path`: Path to the local plugin directory.
- `--check`: Print the computed hash without modifying the manifest.

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
  Baselith Plugin Ecosystem
├── ✅ auth v0.29.0  [security, core]
├──  langchain v0.29.0
├── ✅ rag v0.29.0  [ai, retrieval]
│   ├── ✅ auth v0.29.0
│   └──  langchain v0.29.0
└──  experimental v0.0.1  [alpha]
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

Generate a `manifest.json` file from a legacy plugin's Python metadata definition. This command is primarily a compatibility bridge for older plugins and older scaffold flows; hand-maintained plugins may prefer `manifest.yaml`.

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

### `plugin marketplace list` - List Marketplace Plugins

List all plugins available in the Baselith Marketplace, optionally filtered by
category.

```bash
baselith plugin marketplace list [--category <category>] [--refresh]
```

**Options**:

- `--category`: Filter by category (default: `all`).
- `--refresh`: Bypass the local registry cache and force a refresh.

---

### `plugin marketplace search` - Search Marketplace

Search for plugins available in the Baselith Marketplace.

```bash
baselith plugin marketplace search <query> [--category <category>]
```

**Options**:

- `--category`: Filter by category (default: `all`).

---

### `plugin marketplace info` - Marketplace Plugin Details

Get detailed metadata for a specific marketplace plugin.

```bash
baselith plugin marketplace info <plugin_id>
```

---

### `plugin marketplace install` - Install Plugin

Install a plugin directly from the marketplace, including its dependencies.

For supply-chain hardening, the installer only accepts marketplace entries whose `git_url` uses `https` and does not embed credentials.

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

### `plugin marketplace login` / `logout` / `identity` - Authentication

Manage marketplace credentials, stored under `~/.baselith/credentials.json`
(mode `0600`). `login` can exchange a GitHub token for a marketplace session, or
accept a pasted JWT (auto-detected by structure) or a legacy API key.

```bash
baselith plugin marketplace login --github-token <token>   # Exchange a GitHub token for a session JWT
baselith plugin marketplace login      # Prompt to paste a JWT or API key
baselith plugin marketplace logout     # Remove all cached credentials
baselith plugin marketplace identity   # Show the current identity / token status
```

**Options**:

- `--github-token`: A GitHub token (a classic PAT with no scopes suffices) that
  is exchanged with the hub for a ~7-day marketplace session JWT. The GitHub
  token is used once and never stored; only the resulting JWT is saved.

---

### `plugin marketplace publish` - Publish Plugin

Submit a local plugin to the official marketplace.

```bash
baselith plugin marketplace publish <path> [--key <api_key>]
```

**Options**:

- `--key`: Optional authentication key (otherwise the saved login credentials
  or `MARKETPLACE_API_KEY` are used).

!!! note "Security Restriction"
    The `publish` command is locked to the official marketplace URL for security. Unlike search and install, it cannot be overridden via `MARKETPLACE_CENTRAL_URL`.

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
| `--skip-preflight`   | Start Uvicorn without running the doctor checks first |
| `--check-plugins`    | Include plugin readiness in the startup preflight     |
| `--require-services` | Block startup when preflight cannot reach a backing service |

By default `run` executes the core doctor checks before handing over to
Uvicorn and starts anyway when a backing service is unreachable — the preflight
reports, it does not gate. `--require-services` turns that report into a gate;
`--skip-preflight` removes it entirely.

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

- Auto-loads `settings` (the core config), `LLMService` / `get_llm_service`, and `VectorStoreProtocol`; each import is best-effort, so anything that fails to import is left out and the prompt lists what was actually loaded
- Ideal for quick testing of connections and logic

---

## Database

### `db status` - Database Status

Show the connection status of all persistent data stores (Redis, Qdrant, PostgreSQL, GraphDB).

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

### `db migrate` - Apply Migrations

Run `alembic upgrade head` against the configured PostgreSQL database.

```bash
baselith db migrate
baselith db migrate --json
```

The command checks that `alembic.ini` is present and that PostgreSQL answers
before it starts, so an unreachable database fails with the connection error
rather than a migration traceback. It is the explicit counterpart of
`DB_MIGRATIONS_ON_STARTUP`: set that to `false` and run this at deploy time for
a startup that cannot race two processes onto the same schema.

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
│  Current Configuration   │
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

### `config env` - Create or Normalize an Env Profile

Write the profile defaults into the local env file, leaving every value you
have already set in place.

```bash
baselith config env                 # dev profile -> .env
baselith config env docker-core     # docker-core profile -> configs/.env.docker.core
baselith config env --json
```

This is the step `baselith setup` runs first, exposed on its own for the case
where the env file has drifted and nothing else needs doing. It reports the
keys it added or changed; a file that already matches the profile is left
untouched. Generated files are written `0600` — see the note under the
[`up`](#up---docker-runtime) command.

### `config check-env` - Detect Misspelled Variables

Report environment variables that look like a misspelled setting — a name close
to a real one, which pydantic-settings would silently ignore.

```bash
baselith config check-env
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
│                     Cache Statistics                      │
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
│                       Queue Status                        │
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

Start worker process(es) to consume tasks from the queue.

```bash
baselith queue worker --concurrency 4
```

**Parameters**:

- `--concurrency`: Number of worker **processes** to run (default: 1). One
  runs in the foreground process; the rest are child processes, joined on
  shutdown.

Workers started this way are tenant-aware (they restore `tenant_id`/`user_id`
context before running a job), record terminal failures to the dead-letter
queue, and **run RQ's scheduler**.

!!! warning "The scheduler is not optional"
    `enqueue_in`/`enqueue_at` park jobs in RQ's `ScheduledJobRegistry`. A
    worker started without the scheduler consumes immediate jobs and silently
    ignores delayed ones — so anything that reschedules itself (retry with
    backoff, a simulation tick chain) runs exactly once and then stops, with
    no error anywhere. Every worker this command starts runs the scheduler.

!!! tip "Production"
    In production, use a process manager like `supervisor` or `systemd` to manage workers.

!!! note "Running from a source checkout"
    The `baselith` console script lives in the environment's `bin/`, so
    `import core` resolves to the **installed** distribution even when you are
    standing in a newer checkout — while `plugins.*` (whose package path is
    extended via `pkgutil`) resolves from the checkout. That mix breaks plugins
    with an `ImportError` for a symbol the old core lacks, and inside an RQ
    worker it surfaces as the misleading `ValueError: Invalid attribute name:
    <job function>`. The CLI detects the mismatch at start-up and re-execs
    itself with the checkout first on `PYTHONPATH`, printing one line to
    stderr. Set `BASELITH_CLI_NO_REEXEC=1` to keep the installed copy.

---

## Docs

### `docs generate` - Generate Documentation

Generate OpenAPI documentation for all registered REST endpoints.

```bash
baselith docs generate
```

**Output**:

```text
✅ Found 59 endpoints
✅ Generated: mkdocs-site/docs/api/specs/openapi.json
✅ Generated: mkdocs-site/docs/api/specs/openapi.yaml

To import into Postman, use the generated openapi.json file.
```

The endpoint count depends on the enabled feature flags. `openapi.yaml` is
only written when PyYAML is installed; no Postman collection is produced.
Run the command from the repository root — files are written under
`./mkdocs-site/docs/api/specs/`.

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

!!! tip "JSON output"
    Use `--format json` for machine-readable output, before or after any
    subcommand:
    ```bash
    baselith --format json plugin list
    baselith plugin list --format json
    ```

!!! note "Hot-reload is REST-only"
    There is no `reload` subcommand under `baselith plugin`. Plugin hot-reload is
    exposed through the REST API (`POST /api/plugins/{name}/reload`).
