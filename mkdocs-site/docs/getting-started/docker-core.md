# Docker Core and Plugins

This workflow uses a Core checkout, Python 3.12 or newer for the Baselith CLI,
Git, and a running Docker engine with Compose. Node and frontend package managers
run inside a temporary Docker builder.

## Prepare the Core

From the repository root, inside your Python virtual environment:

```bash
python -m pip install -e .
baselith setup docker-core
```

Setup creates or completes `.env` and `configs/.env.docker.core`. It preserves
valid credentials. It does not download the complete image or start services.
Conda is optional; a Python virtual environment also works.

To start the Core without adding a plugin:

```bash
baselith up
```

`up` creates the missing runtime files, persists the selected
`BASELITH_CORE_IMAGE`, pulls the service images, builds the API image, starts
the stack and waits for `/health`. The Compose command it wraps stays available
for the cases where you need the flags yourself:

```bash
BASELITH_DOCKER_ENV_FILE=configs/.env.docker.core docker compose --env-file configs/.env.docker.core -f docker-compose.core.yml up -d --build
curl --fail http://localhost:8000/health
```

The stack contains the API, PostgreSQL, FalkorDB and Qdrant. Migrations run in the
API entrypoint. Ollama, workers, sandbox and observability services are not started
by this profile. First-build time depends on downloads, architecture and cache.

## Env Keys of the Docker Profile

`baselith setup docker-core` (and `baselith config env docker-core`) writes
`configs/.env.docker.core`. Besides the application settings, the profile owns
the keys that shape the runtime itself — none of them belongs in the root
`.env`, which configures a host-side process instead:

| Key                        | Default                                   | Effect                                                     |
| -------------------------- | ----------------------------------------- | ---------------------------------------------------------- |
| `BASELITH_CORE_IMAGE`      | `ghcr.io/baselithcore/baselithcore:<ver>` | Base image of the API service                              |
| `BASELITH_DOCKER_ENV_FILE` | `configs/.env.docker.core`                | Env file Compose reads; also the file `up` persists keys to |
| `BASELITH_HTTP_PORT`       | `8000`                                    | Host port published for the API                            |
| `BASELITH_POSTGRES_PORT`   | `5432`                                    | Host port published for PostgreSQL, bound to `127.0.0.1`   |
| `BASELITH_REDIS_PORT`      | `6379`                                    | Host port published for Redis/FalkorDB, bound to `127.0.0.1` |
| `BASELITH_QDRANT_PORT`     | `6333`                                    | Host port published for Qdrant, bound to `127.0.0.1`       |
| `BASELITH_RUN_MIGRATIONS`  | `true`                                    | Whether the API entrypoint applies migrations at startup   |

Only the API port is published on every interface; the three backing stores are
bound to the loopback address. Change the `BASELITH_*_PORT` values to run a
second stack side by side, and set `BASELITH_RUN_MIGRATIONS=false` when you
prefer to run `baselith db migrate` yourself at deploy time.

The file holds a generated `DB_PASSWORD` and `SECRET_KEY` and is written `0600`.
Keep that mode when you edit it by hand, and never commit it.

## Add a Plugin

After setup, this command also builds and starts the Core if needed:

```bash
baselith plugin add https://github.com/your-org/plugin-example --docker
```

Use `--ref <branch-or-tag>` for an explicit Git reference on the initial clone.
An existing plugin directory is reused; running `add` again does not pull updates.

The command validates the installation inputs, prepares missing plugin dependencies,
copies the plugin's `.env.example` when `.env` is absent, enables the plugin,
prepares Python requirements, builds its frontend, rebuilds the API image, starts
Compose and probes HTTP. Python packages are installed in the image, not manually
in the running container. `pip check` rejects declared package conflicts.

Frontend builds declared by dependencies run before the main plugin's build.
Legacy dependencies without a frontend declaration retain their shipped assets;
the installer does not infer a new output location for them. A plugin must declare
its own build contract when it needs rebuilding. See [Packaging](../plugins/packaging.md).

`Plugin ready` means the Core health endpoint and the plugin endpoint returned
HTTP 200. It does not certify login, an LLM request or a complete application workflow.
Configure real service credentials in `plugins/<name>/.env` when required.

## Develop a Local Plugin

```bash
baselith plugin create my_plugin --type router
```

The scaffold creates `manifest.yaml`. Implement the plugin and complete its
dependencies, frontend build contract and health endpoint. A Git remote is not
required to work on these files. For the existing directory, the installer can
reuse it and run the Docker workflow:

```bash
baselith plugin add my_plugin --name my_plugin --docker
```

This reuses `plugins/my_plugin`; it is not a general import command for arbitrary
non-Git directories. Build commands from plugins execute code: install trusted repositories.

## Diagnose or Retry

Use the same env file and Compose project for every command:

```bash
BASELITH_DOCKER_ENV_FILE=configs/.env.docker.core docker compose --env-file configs/.env.docker.core -f docker-compose.core.yml ps
BASELITH_DOCKER_ENV_FILE=configs/.env.docker.core docker compose --env-file configs/.env.docker.core -f docker-compose.core.yml logs --tail=100 api
```

Keep each command on one line. `api` is an argument, not a separate command.
For multiple stacks, change the `BASELITH_*_PORT` values in the Docker env and use
that HTTP port in curl. A response on port 8000 may belong to another stack.

Do not delete env files or volumes to retry. PostgreSQL keeps the password with
which its volume was initialized; changing an env value does not rotate it.
Keep that credential or perform an explicit database password change.

`baselith doctor` inspects the host environment. Host package warnings do not prove
that packages are missing in Docker. `doctor --fix` is not a prerequisite after
Docker setup and does not recover credentials from containers.

An installation failure returns a nonzero exit code and preserves the checkout.
It does not roll back every configuration change automatically. After fixing the
reported cause, rerun `plugin add ... --docker` using the existing directory.

## Current Boundaries

Missing plugin dependencies resolve through `baselithcore/plugin-<name>` on the
default Git branch. Custom dependency repositories and dependency-specific refs
are not yet supported. In particular, a fix published only on an auth branch is
not automatically selected when another plugin requires auth.

`baselith plugin sync --docker` reconciles every enabled plugin with the Docker
runtime — requirements, declared frontends, image rebuild, health and per-plugin
probes. It has no fingerprinting, so it rebuilds unconditionally and relies on
Docker layer reuse; uniform Docker update/remove commands are not implemented
yet. Repeating `add` invokes builds again the same way.

Requirements are declarative but not fully locked, and plugins share one Python
environment. An image-only distribution without the Core checkout is separate work.
