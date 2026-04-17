"""User-defined agents with persistence and a whitelisted action catalog.

Custom agents are stored as JSON under ``state_dir/custom_agents.json`` and
rehydrated into the ``AgentRegistry`` on plugin initialization. Only
actions listed in ``ACTION_CATALOG`` are dispatchable; arbitrary code
execution is not permitted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from core.observability.logging import get_logger

from .registry import AgentEntry, AgentInvoker, AgentRegistry

if TYPE_CHECKING:
    from ..chat_commands import ChatCommandRouter

logger = get_logger(__name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_CUSTOM_NAME_PREFIX = "custom."
_MAX_CUSTOM_AGENTS = 64


class AgentActionSpec(BaseModel):
    """Parameters for a whitelisted agent action."""

    type: str = Field(..., description="Action kind (see ACTION_CATALOG).")
    params: dict[str, Any] = Field(default_factory=dict)


class CustomAgentSpec(BaseModel):
    """Persistent custom agent definition."""

    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=32)
    priority: int = Field(default=100, ge=0, le=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    action: AgentActionSpec

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError("name may only contain letters, digits, '.', '_', '-'")
        return value

    @field_validator("keywords")
    @classmethod
    def _validate_keywords(cls, value: list[str]) -> list[str]:
        cleaned = [kw.strip() for kw in value if isinstance(kw, str) and kw.strip()]
        if any(len(kw) > 80 for kw in cleaned):
            raise ValueError("keyword length must be <= 80 characters")
        return cleaned


@dataclass
class AgentActionDescriptor:
    """Human-facing metadata for an action entry in the catalog."""

    type: str
    label: str
    description: str
    params_schema: dict[str, Any]


ACTION_CATALOG: dict[str, AgentActionDescriptor] = {
    "chat_command": AgentActionDescriptor(
        type="chat_command",
        label="Slash command",
        description=(
            "Dispatch a slash command against the ChatCommandRouter. The "
            "incoming query is forwarded as the 'query' context field."
        ),
        params_schema={
            "command": {"type": "string", "required": True, "max_length": 120},
        },
    ),
    "http_webhook": AgentActionDescriptor(
        type="http_webhook",
        label="HTTP webhook",
        description=(
            "POST JSON {'query': ..., 'context': ...} to an HTTPS URL via the "
            "shared httpx pool. Timeout capped at 15s; TLS verification enforced."
        ),
        params_schema={
            "url": {"type": "string", "required": True, "max_length": 2048},
            "headers": {"type": "object", "default": {}},
            "timeout_seconds": {
                "type": "number",
                "default": 15.0,
                "min": 1.0,
                "max": 60.0,
            },
        },
    ),
    "static_response": AgentActionDescriptor(
        type="static_response",
        label="Static response",
        description=(
            "Return a fixed JSON payload regardless of the query. Useful for "
            "smoke-testing routing or stubbing agents under development."
        ),
        params_schema={
            "payload": {"type": "object", "default": {}},
        },
    ),
}


class CustomAgentStore:
    """JSON-backed persistence for user-defined agent specs."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[CustomAgentSpec]:
        if not self._path.is_file():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("baselithbot_custom_agents_load_failed", error=str(exc))
            return []
        specs: list[CustomAgentSpec] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                specs.append(CustomAgentSpec.model_validate(item))
            except Exception as exc:
                logger.warning(
                    "baselithbot_custom_agents_invalid_entry",
                    entry=item,
                    error=str(exc),
                )
        return specs

    def save(self, specs: list[CustomAgentSpec]) -> None:
        payload = [spec.model_dump(mode="json") for spec in specs]
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)


class CustomAgentRegistry:
    """Glue layer: persist specs, build invokers, register with AgentRegistry."""

    def __init__(
        self,
        *,
        agents: AgentRegistry,
        store: CustomAgentStore,
        chat_commands: "ChatCommandRouter | None" = None,
    ) -> None:
        self._agents = agents
        self._store = store
        self._chat_commands = chat_commands
        self._specs: dict[str, CustomAgentSpec] = {}

    def bootstrap(self) -> int:
        """Load persisted specs and register them with the agent registry."""
        self._specs.clear()
        loaded = self._store.load()
        for spec in loaded:
            self._specs[spec.name] = spec
            self._apply(spec)
        return len(loaded)

    def list(self) -> list[CustomAgentSpec]:
        return list(self._specs.values())

    def get(self, name: str) -> CustomAgentSpec | None:
        return self._specs.get(name)

    def is_custom(self, name: str) -> bool:
        return name in self._specs

    def register(self, spec: CustomAgentSpec) -> CustomAgentSpec:
        spec = self._normalize(spec)
        if spec.name in self._specs:
            raise ValueError(f"custom agent '{spec.name}' already exists")
        if len(self._specs) >= _MAX_CUSTOM_AGENTS:
            raise ValueError(
                f"custom agent limit reached ({_MAX_CUSTOM_AGENTS} agents)"
            )
        self._validate_action(spec.action)
        self._specs[spec.name] = spec
        self._apply(spec)
        self._persist()
        return spec

    def update(self, name: str, spec: CustomAgentSpec) -> CustomAgentSpec:
        if name not in self._specs:
            raise KeyError(name)
        spec = self._normalize(spec)
        if spec.name != name:
            raise ValueError("renaming custom agents is not supported")
        self._validate_action(spec.action)
        self._agents.remove(name)
        self._specs[name] = spec
        self._apply(spec)
        self._persist()
        return spec

    def delete(self, name: str) -> bool:
        if name not in self._specs:
            return False
        self._agents.remove(name)
        self._specs.pop(name, None)
        self._persist()
        return True

    def _normalize(self, spec: CustomAgentSpec) -> CustomAgentSpec:
        name = spec.name
        if not name.startswith(_CUSTOM_NAME_PREFIX):
            name = f"{_CUSTOM_NAME_PREFIX}{name}"
        if name != spec.name:
            spec = spec.model_copy(update={"name": name})
        return spec

    def _validate_action(self, action: AgentActionSpec) -> None:
        if action.type not in ACTION_CATALOG:
            raise ValueError(
                f"unknown action type '{action.type}'. "
                f"Allowed: {sorted(ACTION_CATALOG)}"
            )
        if action.type == "chat_command":
            cmd = action.params.get("command")
            if not isinstance(cmd, str) or not cmd.startswith("/"):
                raise ValueError(
                    "chat_command action requires 'command' string starting with '/'"
                )
        elif action.type == "http_webhook":
            url = action.params.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise ValueError(
                    "http_webhook requires 'url' starting with http:// or https://"
                )
        elif action.type == "static_response":
            payload = action.params.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("static_response 'payload' must be a JSON object")

    def _apply(self, spec: CustomAgentSpec) -> None:
        invoker = self._build_invoker(spec)
        entry = AgentEntry(
            name=spec.name,
            description=spec.description,
            keywords=spec.keywords,
            priority=spec.priority,
            metadata={
                **spec.metadata,
                "kind": "custom",
                "action_type": spec.action.type,
            },
        )
        self._agents.register(entry, invoker)

    def _persist(self) -> None:
        self._store.save(list(self._specs.values()))

    def _build_invoker(self, spec: CustomAgentSpec) -> AgentInvoker:
        action_type = spec.action.type
        params = dict(spec.action.params)
        if action_type == "chat_command":
            return _make_chat_command_invoker(spec.name, params, self._chat_commands)
        if action_type == "http_webhook":
            return _make_webhook_invoker(spec.name, params)
        if action_type == "static_response":
            return _make_static_invoker(spec.name, params)
        raise ValueError(f"unknown action type '{action_type}'")


def _make_chat_command_invoker(
    name: str,
    params: dict[str, Any],
    router: "ChatCommandRouter | None",
) -> AgentInvoker:
    command = str(params.get("command", ""))

    async def _run(query: str, context: dict[str, Any]) -> dict[str, Any]:
        if router is None:
            return {
                "status": "error",
                "agent": name,
                "error": "chat_command router unavailable",
            }
        merged: dict[str, Any] = {**context, "query": query}
        result = await router.handle(command, merged)
        logger.info(
            "baselithbot_custom_agent_chat_command",
            agent=name,
            command=command,
            result_status=result.get("status"),
        )
        return {"status": "success", "agent": name, "result": result}

    return _run


def _make_webhook_invoker(name: str, params: dict[str, Any]) -> AgentInvoker:
    url = str(params["url"])
    raw_headers = params.get("headers")
    headers: dict[str, str] = (
        {str(k): str(v) for k, v in raw_headers.items()}
        if isinstance(raw_headers, dict)
        else {}
    )
    timeout = float(params.get("timeout_seconds", 15.0))
    timeout = max(1.0, min(60.0, timeout))

    async def _run(query: str, context: dict[str, Any]) -> dict[str, Any]:
        from ..http_pool import get_shared_client

        client = await get_shared_client(timeout=timeout)
        response = await client.post(
            url,
            json={"query": query, "context": context},
            headers=headers,
        )
        logger.info(
            "baselithbot_custom_agent_webhook",
            agent=name,
            url=url,
            status_code=response.status_code,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        return {
            "status": "success" if response.is_success else "error",
            "agent": name,
            "http_status": response.status_code,
            "result": body,
        }

    return _run


def _make_static_invoker(name: str, params: dict[str, Any]) -> AgentInvoker:
    raw_payload = params.get("payload")
    payload: dict[str, Any] = dict(raw_payload) if isinstance(raw_payload, dict) else {}

    async def _run(_query: str, _context: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "agent": name,
            "result": dict(payload),
        }

    return _run


__all__ = [
    "ACTION_CATALOG",
    "AgentActionDescriptor",
    "AgentActionSpec",
    "CustomAgentRegistry",
    "CustomAgentSpec",
    "CustomAgentStore",
]
