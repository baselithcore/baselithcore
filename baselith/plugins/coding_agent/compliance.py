"""The coding agent in the AI-system inventory, and its output marked as AI-generated.

Both go through ``core`` seams only (``baselith.core.compliance``, ``baselith.core.transparency``), so the plugin
works the same where no compliance console is installed. Registration is idempotent across
processes (deterministic uuid5 id, facts refreshed in place) and never fails the boot; marking
never fails a task.
"""

from __future__ import annotations

import uuid
from typing import Any

from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

SYSTEM_NAME = "Coding agent"
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "baselith:baselith/plugins/coding_agent")
SYSTEM_ID = uuid.uuid5(_NAMESPACE, SYSTEM_NAME).hex

_PURPOSE = "Help people and other agents write, fix, test, explain and refactor source code snippets."
_DESCRIPTION = (
    "A language model proposes code and pytest tests; the code runs only inside an isolated "
    "sandbox (no network by default, read-only filesystem, time and resource limits), and an "
    "answer counts as done only when its tests pass there. Every returned piece of code passes "
    "a deterministic security review, which withholds code carrying a credential or an "
    "equivalent finding, and carries a provenance tag marking it as AI-generated. It makes no "
    "decision about people and acts on no system: the code is handed back for a person to use."
)
#: Facts refreshed on a stored record when this version states them differently; everything
#: else (lifecycle, conformity, contacts) is the operator's and never touched.
_FACTS = (
    "version",
    "intended_purpose",
    "description",
    "generates_synthetic_content",
    "models",
)


def _system(version: str) -> Any:
    from baselith.core.compliance import AiSystem, OperatorRole

    return AiSystem(
        id=SYSTEM_ID,
        name=SYSTEM_NAME,
        role=OperatorRole.DEPLOYER,
        version=version or "0.0.0",
        intended_purpose=_PURPOSE,
        description=_DESCRIPTION,
        interacts_with_humans=False,
        generates_synthetic_content=True,
        models=["configured LLM (central funnel, or the caller's scoped service)"],
        tags=["coding-agent", "code-generation"],
    )


async def register_ai_system(version: str) -> str | None:
    """Register (or refresh) the coding agent in the AI inventory; its id, or None on failure."""
    try:
        from baselith.core.compliance import get_ai_system_registry

        registry = get_ai_system_registry()
        wanted = _system(version)
        stored = await registry.get(SYSTEM_ID)
        if stored is not None:
            stale = [k for k in _FACTS if getattr(stored, k) != getattr(wanted, k)]
            if not stale:
                return str(stored.id)
            for key in stale:
                setattr(stored, key, getattr(wanted, key))
            wanted = stored
        system, _ = await registry.register(wanted)
        logger.info("coding_agent_ai_system_registered", system_id=system.id)
        return str(system.id)
    except Exception as exc:
        logger.warning(
            "coding_agent_ai_system_register_failed", error_type=type(exc).__name__
        )
        return None


def mark_generated(code: str) -> dict[str, Any] | None:
    """The AI Act Art. 50 provenance tag of ``code`` as a plain dict, or None when unavailable."""
    if not code:
        return None
    try:
        from baselith.core.transparency import (
            ContentClass,
            Modality,
            get_transparency_service,
        )

        tag = get_transparency_service().mark_content(
            code, content_class=ContentClass.AI_GENERATED, modality=Modality.TEXT
        )
        return dict(tag.model_dump(mode="json"))
    except Exception as exc:
        logger.warning("coding_agent_mark_failed", error_type=type(exc).__name__)
        return None


__all__ = ["SYSTEM_ID", "SYSTEM_NAME", "mark_generated", "register_ai_system"]
