"""Running the code a thought asks for (``[EXECUTE]…[/EXECUTE]``).

Model-written code is the most dangerous call in the framework, so it goes
through the same chokepoint as every other tool — contract, autonomy approval,
budget, audit — and what the sandbox printed (attacker-influenced text headed
for later prompts and the answer) is bounded, scanned and sealed as data.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.autonomy import AutonomyPolicy
from baselith.core.orchestration.enforcement import enforce_tool_invocation
from baselith.core.orchestration.tool_output import (
    sanitize_tool_output,
    truncate_tool_output,
    wrap_untrusted,
)

logger = get_logger(__name__)

#: The orchestration context gating ``[EXECUTE]`` for the search in progress
#: (a contextvar: the engine is shared and its expansions run concurrently).
ENFORCEMENT_CONTEXT: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "tot_enforcement_context", default=None
)

__all__ = ["ENFORCEMENT_CONTEXT", "run_thought_code"]


async def run_thought_code(sandbox: Any, code: str) -> str:
    """Execute ``code`` in ``sandbox`` under the gate; return sealed output.

    With no orchestration context the default ``SUPERVISED`` policy applies, so
    an unattended search fails closed (no approval channel) rather than running
    code nobody approved. A refusal or a sandbox failure becomes the output
    text: the thought records it and the search carries on.
    """
    output = "Tool execution failed"
    try:
        await enforce_tool_invocation(
            ENFORCEMENT_CONTEXT.get() or {"autonomy_policy": AutonomyPolicy()},
            "execute_code",
            "mutating",
            args={"code": code},
        )
        result = await sandbox.execute_code_async(code)
        output = result.stdout if result.exit_code == 0 else result.stderr
    except Exception as exc:
        logger.warning("ToT sandbox execution raised", exc_info=True)
        output = f"Error: {exc}"
    return wrap_untrusted(
        sanitize_tool_output(truncate_tool_output(str(output)), source="execute_code"),
        source="execute_code",
    )
