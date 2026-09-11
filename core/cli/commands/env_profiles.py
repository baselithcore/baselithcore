"""Environment profile helpers for local setup flows."""

from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe

from core.cli.commands.doctor_checks import is_placeholder_secret


DEV_DEFAULTS = {
    "APP_ENV": "development",
    "CORE_DEBUG": "true",
    "CORE_PLUGIN_DIR": "./plugins",
    "CORE_DATA_DIR": "./data",
    "CORE_DOCUMENTS_DIR": "./documents",
    "POSTGRES_ENABLED": "true",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_NAME": "baselithcore",
    "DB_USER": "baselithcore",
    "DB_SSL_MODE": "disable",
    "CACHE_BACKEND": "local",
    "CACHE_REDIS_URL": "redis://localhost:6379/1",
    "QUEUE_REDIS_URL": "redis://localhost:6379/2",
    "GRAPH_DB_ENABLED": "true",
    "GRAPH_DB_URL": "redis://localhost:6379",
    "VECTORSTORE_PROVIDER": "qdrant",
    "VECTORSTORE_QDRANT_HOST": "localhost",
    "VECTORSTORE_PORT": "6333",
    "VECTORSTORE_GRPC_PORT": "6334",
    "VECTORSTORE_QDRANT_MODE": "server",
    "TELEMETRY_ENABLED": "false",
    "DB_MIGRATIONS_ON_STARTUP": "false",
}

DOCKER_CORE_DEFAULTS = {
    "CORE_APP_NAME": "Baselith-Core",
    "CORE_DEBUG": "false",
    "CORE_PLUGIN_DIR": "/app/plugins",
    "CORE_DATA_DIR": "/app/data",
    "CORE_DOCUMENTS_DIR": "/app/documents",
    "CORE_DETERMINISTIC_MODE": "false",
    "HOST": "0.0.0.0",
    "PORT": "8000",
    "BASELITH_HTTP_PORT": "8000",
    "BASELITH_POSTGRES_PORT": "5432",
    "BASELITH_REDIS_PORT": "6379",
    "BASELITH_QDRANT_PORT": "6333",
    "WEB_CONCURRENCY": "1",
    "LOG_LEVEL_CONSOLE": "INFO",
    "LOG_LEVEL_FILE": "INFO",
    "LOG_JSON": "true",
    "LOG_MASKING_ENABLED": "true",
    "TELEMETRY_ENABLED": "false",
    "POSTGRES_ENABLED": "true",
    "DB_HOST": "postgres",
    "DB_PORT": "5432",
    "DB_NAME": "baselithcore",
    "DB_USER": "baselithcore",
    "DB_SSL_MODE": "disable",
    "DB_POOL_MIN_SIZE": "1",
    "DB_POOL_MAX_SIZE": "5",
    "DB_POOL_TIMEOUT": "30.0",
    "GRAPH_DB_ENABLED": "true",
    "GRAPH_DB_URL": "redis://redis:6379",
    "GRAPH_DB_NAME": "agent_graph",
    "GRAPH_RAG_ENABLED": "false",
    "GRAPH_DB_TIMEOUT": "2.0",
    "CACHE_BACKEND": "redis",
    "CACHE_REDIS_URL": "redis://redis:6379/1",
    "QUEUE_REDIS_URL": "redis://redis:6379/2",
    "CACHE_REDIS_PREFIX": "baselithcore",
    "VECTORSTORE_PROVIDER": "qdrant",
    "VECTORSTORE_QDRANT_HOST": "qdrant",
    "VECTORSTORE_PORT": "6333",
    "VECTORSTORE_GRPC_PORT": "6334",
    "VECTORSTORE_COLLECTION_NAME": "documents",
    "VECTORSTORE_QDRANT_MODE": "server",
    "VECTORSTORE_EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
    "VECTORSTORE_EMBEDDING_DIM": "384",
    "LLM_PROVIDER": "openai",
    "LLM_MODEL": "gpt-4o-mini",
    "LLM_API_KEY": "",
    "LLM_API_BASE": "",
    "LLM_ENABLE_CACHE": "true",
    "LLM_TEMPERATURE": "0.7",
    "VISION_PROVIDER": "openai",
    "VISION_OPENAI_API_KEY": "",
    "VISION_OLLAMA_HOST": "http://host.docker.internal:11434",
    "PLUGIN_ENABLED": "true",
    "PLUGIN_AUTO_LOAD": "true",
    "PLUGIN_CONFIG_PATH": "/app/configs/plugins.yaml",
    "AUTH_REQUIRED": "false",
    "ALLOW_ORIGINS": '["*"]',
    "CHAT_GUARDRAILS_ENABLED": "false",
    "BASELITH_RUN_MIGRATIONS": "true",
}


def ensure_dev_env(env_path: Path | None = None) -> list[str]:
    """Ensure the root ``.env`` has portable developer defaults."""
    path = env_path or Path.cwd() / ".env"
    if not path.exists():
        source = Path.cwd() / ".env.example"
        if not source.exists():
            source = Path.cwd() / "configs" / ".env.base"
        if source.exists():
            path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            path.write_text("", encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    values = _parse_env(lines)
    changed: list[str] = []

    for key, value in DEV_DEFAULTS.items():
        if key not in values or values[key] == "":
            lines.append(f"{key}={value}")
            values[key] = value
            changed.append(key)

    current_password = values.get("DB_PASSWORD")
    if is_placeholder_secret(current_password):
        generated = token_urlsafe(32)
        lines, replaced = _set_env_value(lines, "DB_PASSWORD", generated)
        if not replaced:
            lines.append(f"DB_PASSWORD={generated}")
        changed.append("DB_PASSWORD")

    current_secret = values.get("SECRET_KEY")
    if is_placeholder_secret(current_secret):
        generated = token_urlsafe(48)
        lines, replaced = _set_env_value(lines, "SECRET_KEY", generated)
        if not replaced:
            lines.append(f"SECRET_KEY={generated}")
        changed.append("SECRET_KEY")

    if changed:
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return changed


def ensure_docker_core_env(env_path: Path | None = None) -> list[str]:
    """Create or normalize the local Docker core env file."""
    path = env_path or Path.cwd() / "configs" / ".env.docker.core"
    if not path.exists():
        source = Path.cwd() / "configs" / ".env.docker.core.example"
        if source.exists():
            path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    values = _parse_env(lines)
    changed: list[str] = []

    if "COMPOSE_PROJECT_NAME" not in values:
        project_name = _compose_project_name(Path.cwd())
        lines.append(f"COMPOSE_PROJECT_NAME={project_name}")
        values["COMPOSE_PROJECT_NAME"] = project_name
        changed.append("COMPOSE_PROJECT_NAME")

    for key, value in DOCKER_CORE_DEFAULTS.items():
        if key not in values:
            lines.append(f"{key}={value}")
            values[key] = value
            changed.append(key)

    current_password = values.get("DB_PASSWORD")
    if is_placeholder_secret(current_password):
        generated = token_urlsafe(32)
        lines, replaced = _set_env_value(lines, "DB_PASSWORD", generated)
        if not replaced:
            lines.append(f"DB_PASSWORD={generated}")
        changed.append("DB_PASSWORD")

    current_secret = values.get("SECRET_KEY")
    if is_placeholder_secret(current_secret):
        generated = _local_secret_key() or token_urlsafe(48)
        lines, replaced = _set_env_value(lines, "SECRET_KEY", generated)
        if not replaced:
            lines.append(f"SECRET_KEY={generated}")
        changed.append("SECRET_KEY")

    if changed:
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return changed


def _local_secret_key() -> str | None:
    local_env = Path.cwd() / ".env"
    if not local_env.is_file():
        return None
    values = _parse_env(local_env.read_text(encoding="utf-8").splitlines())
    secret_key = values.get("SECRET_KEY")
    if is_placeholder_secret(secret_key):
        return None
    return secret_key


def _compose_project_name(project_root: Path) -> str:
    digest = sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:8]
    stem = "".join(
        char.lower() if char.isalnum() else "-" for char in project_root.name
    ).strip("-")
    return f"{stem or 'baselithcore'}-{digest}"


def _parse_env(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _set_env_value(lines: list[str], key: str, value: str) -> tuple[list[str], bool]:
    updated: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            current, _ = stripped.split("=", 1)
            if current.strip() == key:
                updated.append(f"{key}={value}")
                replaced = True
                continue
        updated.append(line)
    return updated, replaced


__all__ = ["ensure_dev_env", "ensure_docker_core_env"]
