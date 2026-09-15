# Docker Core and Plugins

There are two supported workflows. Core contributors can run from a source
checkout. Plugin developers and customer installations can instead generate a
standalone project backed by the released Core image. Both require the Baselith
CLI, Git, and Docker with Compose. Node and frontend package managers run inside
a temporary Docker builder.

## Start without a Core Checkout

The shortest path prepares the current directory, downloads every required
image, starts the stack and waits for Core health:

```bash
mkdir my-core && cd my-core
baselith up
```

For a prerelease or pinned image:

```bash
baselith up --image ghcr.io/baselithcore/baselithcore:docker-runtime-test
```

`up` downloads the Core base image plus PostgreSQL/pgvector, FalkorDB and
Qdrant. Each service remains in its own container. Existing Baselith runtime
configuration is preserved, and unrelated Dockerfile or Compose files are not
overwritten.

Create a small runtime project containing only Compose configuration, persistent
directories and plugin sources:

```bash
baselith init my-core --template docker-runtime
cd my-core
docker compose --env-file configs/.env.docker.core -f docker-compose.core.yml up -d --build
curl --fail http://localhost:8000/health
```

The generated Dockerfile extends the version-matched
`ghcr.io/baselithcore/baselithcore` image. It does not copy the Core source into
the project. Adding a plugin updates a generated requirements file and rebuilds
only this local derived image, so plugin Python packages survive container
replacement. Plugin files and frontend output remain mounted from `plugins/`
for immediate development.

## Start from a Core Checkout

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
BASELITH_DOCKER_ENV_FILE=configs/.env.docker.core docker compose --env-file configs/.env.docker.core -f docker-compose.core.yml up -d --build
curl --fail http://localhost:8000/health
```

The stack contains the API, PostgreSQL, FalkorDB and Qdrant. Migrations run in the
API entrypoint. Ollama, workers, sandbox and observability services are not started
by this profile. First-build time depends on downloads, architecture and cache.

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

Fingerprint-based sync and uniform Docker update/remove commands are not implemented
yet. Repeating `add` invokes builds again; Docker may reuse layers. Requirements
are declarative but not fully locked, and plugins share one Python environment.
