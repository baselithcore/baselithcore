"""User-defined cron jobs with persistence and a whitelisted action catalog.

Custom jobs are stored as JSON under ``state_dir/custom_crons.json`` and
rehydrated into the ``CronScheduler`` on plugin initialization. Only
actions listed in ``ACTION_CATALOG`` are dispatchable; arbitrary code
execution is not permitted.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel, Field, field_validator

from core.observability.logging import get_logger

from .cron import CronScheduler

if TYPE_CHECKING:
    from .chat_commands import ChatCommandRouter

logger = get_logger(__name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_CUSTOM_NAME_PREFIX = "custom."
_MAX_CUSTOM_JOBS = 64


class CronActionSpec(BaseModel):
    """Parameters for a whitelisted cron action."""

    type: str = Field(..., description="Action kind (see ACTION_CATALOG).")
    params: dict[str, Any] = Field(default_factory=dict)


class CustomCronSpec(BaseModel):
    """Persistent custom cron definition."""

    name: str = Field(..., min_length=1, max_length=120)
    interval_seconds: float = Field(..., ge=1.0, le=86400.0)
    action: CronActionSpec
    description: str = Field(default="", max_length=500)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(
                "name may only contain letters, digits, '.', '_', '-'"
            )
        return value


@dataclass
class CronActionDescriptor:
    """Human-facing metadata for an action entry in the catalog."""

    type: str
    label: str
    description: str
    params_schema: dict[str, Any]


ACTION_CATALOG: dict[str, CronActionDescriptor] = {
    "log": CronActionDescriptor(
        type="log",
        label="Log line",
        description="Emit a structured log entry (useful for ping/healthcheck jobs).",
        params_schema={
            "message": {"type": "string", "required": True, "max_length": 500},
            "level": {
                "type": "string",
                "enum": ["debug", "info", "warning"],
                "default": "info",
            },
        },
    ),
    "chat_command": CronActionDescriptor(
        type="chat_command",
        label="Slash command",
        description=(
            "Dispatch a slash command (e.g. /status, /usage) against the "
            "ChatCommandRouter. Command must be in SUPPORTED_COMMANDS."
        ),
        params_schema={
            "command": {"type": "string", "required": True, "max_length": 120},
            "context": {"type": "object", "default": {}},
        },
    ),
    "http_webhook": CronActionDescriptor(
        type="http_webhook",
        label="HTTP webhook",
        description=(
            "POST JSON to an HTTPS URL via the shared httpx pool. "
            "Timeout capped at 15s; TLS verification enforced."
        ),
        params_schema={
            "url": {"type": "string", "required": True, "max_length": 2048},
            "body": {"type": "object", "default": {}},
            "headers": {"type": "object", "default": {}},
            "timeout_seconds": {
                "type": "number",
                "default": 15.0,
                "min": 1.0,
                "max": 60.0,
            },
        },
    ),
}


class CustomCronStore:
    """JSON-backed persistence for user-defined cron specs."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def load(self) -> list[CustomCronSpec]:
        if not self._path.is_file():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("baselithbot_custom_crons_load_failed", error=str(exc))
            return []
        specs: list[CustomCronSpec] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                specs.append(CustomCronSpec.model_validate(item))
            except Exception as exc:
                logger.warning(
                    "baselithbot_custom_crons_invalid_entry",
                    entry=item,
                    error=str(exc),
                )
        return specs

    def save(self, specs: list[CustomCronSpec]) -> None:
        payload = [spec.model_dump(mode="json") for spec in specs]
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)


ActionExecutor = Callable[[], Awaitable[None]]


class CustomCronRegistry:
    """Glue layer: stores specs, builds executors, registers in the scheduler."""

    def __init__(
        self,
        *,
        scheduler: CronScheduler,
        store: CustomCronStore,
        chat_commands: "ChatCommandRouter | None" = None,
    ) -> None:
        self._scheduler = scheduler
        self._store = store
        self._chat_commands = chat_commands
        self._specs: dict[str, CustomCronSpec] = {}

    def bootstrap(self) -> int:
        """Load persisted specs and register them with the scheduler."""
        self._specs.clear()
        loaded = self._store.load()
        for spec in loaded:
            self._specs[spec.name] = spec
            self._apply(spec)
        return len(loaded)

    def list(self) -> list[CustomCronSpec]:
        return list(self._specs.values())

    def get(self, name: str) -> CustomCronSpec | None:
        return self._specs.get(name)

    def register(self, spec: CustomCronSpec) -> CustomCronSpec:
        spec = self._normalize(spec)
        if spec.name in self._specs:
            raise ValueError(f"custom cron '{spec.name}' already exists")
        if len(self._specs) >= _MAX_CUSTOM_JOBS:
            raise ValueError(
                f"custom cron limit reached ({_MAX_CUSTOM_JOBS} jobs)"
            )
        self._validate_action(spec.action)
        self._specs[spec.name] = spec
        self._apply(spec)
        self._persist()
        return spec

    def update(self, name: str, spec: CustomCronSpec) -> CustomCronSpec:
        if name not in self._specs:
            raise KeyError(name)
        spec = self._normalize(spec)
        if spec.name != name:
            raise ValueError("renaming custom crons is not supported")
        self._validate_action(spec.action)
        self._scheduler.remove(name)
        self._specs[name] = spec
        self._apply(spec)
        self._persist()
        return spec

    def delete(self, name: str) -> bool:
        if name not in self._specs:
            return False
        self._scheduler.remove(name)
        self._specs.pop(name, None)
        self._persist()
        return True

    def is_custom(self, name: str) -> bool:
        return name in self._specs

    def _normalize(self, spec: CustomCronSpec) -> CustomCronSpec:
        name = spec.name
        if not name.startswith(_CUSTOM_NAME_PREFIX):
            name = f"{_CUSTOM_NAME_PREFIX}{name}"
        if name != spec.name:
            spec = spec.model_copy(update={"name": name})
        return spec

    def _validate_action(self, action: CronActionSpec) -> None:
        if action.type not in ACTION_CATALOG:
            raise ValueError(
                f"unknown action type '{action.type}'. "
                f"Allowed: {sorted(ACTION_CATALOG)}"
            )
        if action.type == "log":
            if not isinstance(action.params.get("message"), str):
                raise ValueError("log action requires 'message' string param")
        elif action.type == "chat_command":
            cmd = action.params.get("command")
            if not isinstance(cmd, str) or not cmd.startswith("/"):
                raise ValueError(
                    "chat_command action requires 'command' string starting with '/'"
                )
        elif action.type == "http_webhook":
            url = action.params.get("url")
            if not isinstance(url, str) or not url.startswith(
                ("http://", "https://")
            ):
                raise ValueError(
                    "http_webhook requires 'url' starting with http:// or https://"
                )

    def _apply(self, spec: CustomCronSpec) -> None:
        executor = self._build_executor(spec)
        self._scheduler.add_interval(
            spec.name,
            executor,
            seconds=spec.interval_seconds,
            description=spec.description or f"custom:{spec.action.type}",
            enabled=spec.enabled,
        )

    def _persist(self) -> None:
        self._store.save(list(self._specs.values()))

    def _build_executor(self, spec: CustomCronSpec) -> ActionExecutor:
        action_type = spec.action.type
        params = dict(spec.action.params)
        if action_type == "log":
            return _make_log_executor(spec.name, params)
        if action_type == "chat_command":
            return _make_chat_command_executor(
                spec.name, params, self._chat_commands
            )
        if action_type == "http_webhook":
            return _make_webhook_executor(spec.name, params)
        raise ValueError(f"unknown action type '{action_type}'")


def _make_log_executor(name: str, params: dict[str, Any]) -> ActionExecutor:
    message = str(params.get("message", ""))
    level = str(params.get("level", "info")).lower()

    async def _run() -> None:
        log_fn = getattr(logger, level, logger.info)
        log_fn("baselithbot_custom_cron_log", job=name, message=message)

    return _run


def _make_chat_command_executor(
    name: str,
    params: dict[str, Any],
    router: "ChatCommandRouter | None",
) -> ActionExecutor:
    command = str(params.get("command", ""))
    context = params.get("context") if isinstance(params.get("context"), dict) else {}

    async def _run() -> None:
        if router is None:
            raise RuntimeError("chat_command router unavailable")
        result = await router.handle(command, context or {})
        logger.info(
            "baselithbot_custom_cron_chat_command",
            job=name,
            command=command,
            result_status=result.get("status"),
        )

    return _run


def _make_webhook_executor(name: str, params: dict[str, Any]) -> ActionExecutor:
    url = str(params["url"])
    body = params.get("body") if isinstance(params.get("body"), dict) else {}
    headers = (
        params.get("headers") if isinstance(params.get("headers"), dict) else {}
    )
    timeout = float(params.get("timeout_seconds", 15.0))
    timeout = max(1.0, min(60.0, timeout))

    async def _run() -> None:
        from .http_pool import get_shared_client

        client = await get_shared_client(timeout=timeout)
        response = await client.post(url, json=body or {}, headers=headers or {})
        logger.info(
            "baselithbot_custom_cron_webhook",
            job=name,
            url=url,
            status_code=response.status_code,
        )

    return _run


__all__ = [
    "ACTION_CATALOG",
    "CronActionDescriptor",
    "CronActionSpec",
    "CustomCronRegistry",
    "CustomCronSpec",
    "CustomCronStore",
]
