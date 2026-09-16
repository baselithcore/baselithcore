"""Diagnostic checks used by the ``baselith doctor`` command."""

import os
import shutil
import socket
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import NamedTuple


class CheckResult(NamedTuple):
    """Result of a health check."""

    name: str
    passed: bool
    message: str
    details: str = ""
    severity: str = "fail"


def env_file_candidates() -> list[Path]:
    """Return supported local env file paths, in precedence order."""
    return [Path.cwd() / ".env", Path.cwd() / "configs" / ".env"]


_ENV_CONFIG_MARKERS: tuple[str, ...] = (
    "SECRET_KEY",
    "DATABASE_URL",
    "DB_HOST",
    "DB_PASSWORD",
    "CACHE_REDIS_URL",
    "QUEUE_REDIS_URL",
    "QDRANT_URL",
)


def read_env_values() -> dict[str, str]:
    """Read simple KEY=VALUE pairs from local env files."""
    values: dict[str, str] = {}
    for env_path in env_file_candidates():
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def env_value(name: str, default: str | None = None) -> str | None:
    """Resolve an environment value from process env or local env files."""
    return os.environ.get(name) or read_env_values().get(name) or default


def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a port is open and accepting connections."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
        return result == 0
    except OSError:
        return False


def parse_url(url: str, default_port: int) -> tuple[str, int]:
    """Parse a URL to extract host and port."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or default_port
        return host, port
    except ValueError:
        return "localhost", default_port


def is_placeholder_secret(value: object) -> bool:
    """Return true when a configured secret is blank or a template placeholder."""
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    if not value:
        return True
    normalized = str(value).strip().lower()
    if not normalized:
        return True
    if normalized in {"__change_me__", "changeme", "change_me", "todo", "dummy"}:
        return True
    return normalized.startswith("__change_me")


def resolve_local_path(value: str | None, fallback: str) -> Path:
    """Resolve a possibly relative project path."""
    raw = value or fallback
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def check_python_runtime() -> CheckResult:
    """Check that the active Python runtime is usable for BaselithCore."""
    version = sys.version_info
    executable = sys.executable
    if version < (3, 12):
        return CheckResult(
            "Python",
            False,
            f"Python {version.major}.{version.minor} is active",
            f"Use Python 3.12+ for this project. Active executable: {executable}",
        )
    return CheckResult(
        "Python",
        True,
        f"Python {version.major}.{version.minor}.{version.micro}",
        executable,
    )


def check_docker() -> CheckResult:
    """Check whether Docker CLI is available and Docker daemon is reachable."""
    docker = shutil.which("docker")
    if docker is None:
        return CheckResult(
            "Docker",
            False,
            "Docker CLI not found",
            "Install Docker Desktop or make docker available on PATH.",
            "warn",
        )
    try:
        import subprocess

        result = subprocess.run(
            [docker, "info"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as e:
        return CheckResult("Docker", False, f"Error: {e}", severity="warn")
    if result.returncode == 0:
        return CheckResult("Docker", True, "Docker daemon reachable")
    output = f"{result.stdout}\n{result.stderr}".lower()
    if "permission denied" in output:
        return CheckResult(
            "Docker",
            False,
            "Docker socket permission denied",
            "Docker is installed, but this process cannot access the Docker socket.",
            "warn",
        )
    return CheckResult(
        "Docker",
        False,
        "Docker daemon not reachable",
        "Start Docker Desktop before running compose services.",
        "warn",
    )


def check_core_dependencies() -> CheckResult:
    """Check optional core extras that are commonly required locally."""
    packages = ["PyYAML", "qdrant-client", "sentence-transformers", "greenlet"]
    missing: list[str] = []
    for package in packages:
        try:
            distribution(package)
        except PackageNotFoundError:
            missing.append(package)
    if not missing:
        return CheckResult("Core Dependencies", True, "Common local extras installed")
    return CheckResult(
        "Core Dependencies",
        False,
        f"{len(missing)} common local dependency(ies) missing",
        "Install missing packages: " + ", ".join(missing),
        "warn",
    )


def check_env_file() -> CheckResult:
    """Check if a local env file exists."""
    for env_path in env_file_candidates():
        if env_path.exists():
            return CheckResult("Environment", True, f"Found config at {env_path}")
    configured = [name for name in _ENV_CONFIG_MARKERS if os.environ.get(name)]
    if configured:
        return CheckResult(
            "Environment",
            True,
            "Configuration provided by environment variables",
            ", ".join(configured),
        )
    return CheckResult(
        "Environment",
        False,
        ".env file not found",
        "Run: cp .env.example .env, or use: baselith doctor --fix",
    )


def check_data_dirs(create: bool = False) -> CheckResult:
    """Check local data directories are present and writable."""
    data_dir = resolve_local_path(env_value("CORE_DATA_DIR"), "./data")
    required = [data_dir, data_dir / "catalog", data_dir / "compliance"]
    if create:
        for path in required:
            path.mkdir(parents=True, exist_ok=True)
    missing = [path for path in required if not path.exists()]
    if missing:
        return CheckResult(
            "Data Directories",
            False,
            f"{len(missing)} data director(y/ies) missing",
            "Run: baselith doctor --fix",
        )
    unwritable = [path for path in required if not os.access(path, os.W_OK)]
    if unwritable:
        return CheckResult(
            "Data Directories",
            False,
            "Data directory is not writable",
            ", ".join(str(path) for path in unwritable),
        )
    return CheckResult("Data Directories", True, f"Writable at {data_dir}")


def check_redis() -> CheckResult:
    """Check Redis connectivity."""
    try:
        from core.config import get_storage_config

        host, port = parse_url(get_storage_config().cache_redis_url, 6379)
        if check_port(host, port):
            return CheckResult("Redis (Cache)", True, f"Connected ({host}:{port})")
        return CheckResult(
            "Redis (Cache)",
            False,
            f"Cannot connect ({host}:{port})",
            "Run: docker compose up -d redis",
        )
    except Exception as e:
        return CheckResult("Redis", False, f"Error: {e}")


def check_qdrant() -> CheckResult:
    """Check Qdrant vector store connectivity."""
    try:
        from core.config import get_vectorstore_config

        config = get_vectorstore_config()
        if config.provider != "qdrant":
            return CheckResult("Vector Store", True, f"Provider: {config.provider}")
        if check_port(config.host, config.port):
            return CheckResult(
                "Qdrant", True, f"Connected ({config.host}:{config.port})"
            )
        return CheckResult(
            "Qdrant",
            False,
            f"Cannot connect ({config.host}:{config.port})",
            "Run: docker compose up -d qdrant",
        )
    except Exception as e:
        return CheckResult("Qdrant", False, f"Error: {e}")


def check_graph_db() -> CheckResult:
    """Check graph database connectivity when enabled."""
    try:
        from core.config import get_storage_config

        config = get_storage_config()
        if not config.graph_db_enabled:
            return CheckResult("GraphDB", True, "Disabled")
        host, port = parse_url(config.graph_db_url, 6379)
        if check_port(host, port):
            return CheckResult("GraphDB", True, f"Connected ({host}:{port})")
        return CheckResult(
            "GraphDB",
            False,
            f"Cannot connect ({host}:{port})",
            "Run: docker compose up -d redis",
            "warn",
        )
    except Exception as e:
        return CheckResult("GraphDB", False, f"Error: {e}", severity="warn")


def check_postgres() -> CheckResult:
    """Check PostgreSQL database connectivity."""
    try:
        from core.config import get_storage_config

        config = get_storage_config()
        if not config.postgres_enabled:
            return CheckResult("PostgreSQL", True, "Disabled")
        pg_url = config.conninfo
        if not pg_url:
            return CheckResult("PostgreSQL", False, "Missing database configuration")
        host, port = parse_url(
            pg_url.replace("postgresql+asyncpg://", "http://").replace(
                "postgresql://", "http://"
            ),
            5432,
        )
        if check_port(host, port):
            return CheckResult("PostgreSQL", True, f"Connected ({host}:{port})")
        return CheckResult(
            "PostgreSQL",
            False,
            f"Cannot connect ({host}:{port})",
            "Run: docker compose up -d postgres",
        )
    except Exception as e:
        return CheckResult("PostgreSQL", False, f"Error: {e}")


def check_llm_provider() -> CheckResult:
    """Check LLM provider availability."""
    try:
        from core.config import get_llm_config

        config = get_llm_config()
        provider = config.provider
        if provider == "ollama":
            ollama_url = config.api_base or "http://localhost:11434"
            host, port = parse_url(ollama_url, 11434)
            if check_port(host, port):
                return CheckResult(
                    "LLM Provider", True, f"Ollama connected ({host}:{port})"
                )
            return CheckResult(
                "LLM Provider",
                False,
                f"Ollama not reachable ({host}:{port})",
                "Run: ollama serve",
            )
        if provider in ("openai", "huggingface"):
            if not is_placeholder_secret(config.api_key):
                return CheckResult(
                    "LLM Provider", True, f"{provider.upper()} API key configured"
                )
            return CheckResult(
                "LLM Provider",
                False,
                f"{provider.upper()} API key missing",
                "Set LLM_API_KEY in .env",
            )
        return CheckResult("LLM Provider", True, f"Provider: {provider}")
    except Exception as e:
        return CheckResult("LLM Provider", False, f"Error: {e}")


def check_telemetry() -> CheckResult:
    """Warn when telemetry is enabled without a local collector."""
    enabled = (env_value("TELEMETRY_ENABLED", "false") or "false").lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return CheckResult("Telemetry", True, "Disabled")
    endpoint = env_value("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    host, port = parse_url(endpoint or "http://localhost:4317", 4317)
    if check_port(host, port, timeout=1.0):
        return CheckResult("Telemetry", True, f"Collector reachable ({host}:{port})")
    return CheckResult(
        "Telemetry",
        False,
        f"Enabled but collector is not reachable ({host}:{port})",
        "Set TELEMETRY_ENABLED=false locally or start the OpenTelemetry collector.",
        "warn",
    )


def check_migrations_mode() -> CheckResult:
    """Explain whether database migrations are automatic or explicit."""
    value = (env_value("DB_MIGRATIONS_ON_STARTUP", "true") or "true").lower()
    if value in {"1", "true", "yes", "on"}:
        return CheckResult(
            "DB Migrations",
            True,
            "Run during application startup",
            "For predictable startup, prefer false and run: baselith db migrate",
        )
    return CheckResult(
        "DB Migrations",
        True,
        "Startup migrations disabled",
        "Run manually after DB changes: baselith db migrate",
    )


def apply_fixes() -> list[str]:
    """Apply safe local fixes only."""
    fixed: list[str] = []
    root_env = Path.cwd() / ".env"
    if not root_env.exists():
        source = Path.cwd() / ".env.example"
        if not source.exists():
            source = Path.cwd() / "configs" / ".env.base"
        if source.exists():
            shutil.copyfile(source, root_env)
            fixed.append(f"Created {root_env} from {source}")
    data_dir = resolve_local_path(env_value("CORE_DATA_DIR"), "./data")
    data_paths = [data_dir, data_dir / "catalog", data_dir / "compliance"]
    before = [path.exists() for path in data_paths]
    result = check_data_dirs(create=True)
    if result.passed and not all(before):
        fixed.append("Created local data directories")
    return fixed


def run_checks() -> list[CheckResult]:
    """Run all doctor checks in display order."""
    from core.cli.commands.doctor_plugin_checks import (
        check_plugin_dependencies,
        check_plugin_frontends,
        check_plugins,
    )

    return [
        check_python_runtime(),
        check_env_file(),
        check_data_dirs(),
        check_docker(),
        check_core_dependencies(),
        check_llm_provider(),
        check_redis(),
        check_qdrant(),
        check_postgres(),
        check_graph_db(),
        check_telemetry(),
        check_migrations_mode(),
        check_plugins(),
        check_plugin_dependencies(),
        check_plugin_frontends(),
    ]


__all__ = [
    "CheckResult",
    "apply_fixes",
    "check_graph_db",
    "check_postgres",
    "check_qdrant",
    "check_redis",
    "run_checks",
]
