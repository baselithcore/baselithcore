"""CODE node: run a Python snippet in the sandbox.

Off by default (``WORKFLOWS_CODE_ENABLED``). A workflow definition can be
uploaded through the API, and a node that executes code turns "may save a
workflow" into "may run code", so an operator opts in on purpose. The code
never runs in the framework process: it goes to the same isolated sandbox the
coding agents use (:class:`~baselith.core.services.sandbox.service.SandboxService`,
Docker or microVM) with its resource ceilings and static pre-screen.

The snippet receives its inputs as JSON in the ``WORKFLOW_INPUT`` environment
variable and returns a result by printing it::

    import json, os
    data = json.loads(os.environ["WORKFLOW_INPUT"])
    print(json.dumps({"total": sum(data["numbers"])}))

Stdout is parsed as JSON when it parses and returned as text otherwise; a
non-zero exit fails the node with the tail of stderr.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ._scope import build_scope, render_value
from .builder import WorkflowNode

if TYPE_CHECKING:
    from .executor import ExecutionContext, WorkflowExecutor

__all__ = ["INPUT_ENV", "MAX_INPUT_BYTES", "handle_code"]

#: Environment variable carrying the node's inputs, as JSON.
INPUT_ENV = "WORKFLOW_INPUT"
#: Largest input payload (an environment variable is not a data channel).
MAX_INPUT_BYTES = 64 * 1024
_STDERR_TAIL = 500


async def _sandbox_runner(
    code: str, *, envs: dict[str, str], timeout: int | None
) -> Any:
    from baselith.core.services.sandbox.service import SandboxService

    return await SandboxService().execute_code_async(
        code, "python", timeout=timeout, envs=envs
    )


async def handle_code(
    executor: WorkflowExecutor, node: WorkflowNode, context: ExecutionContext
) -> Any:
    """Run ``config['code']`` in the sandbox.

    Config:
        code: Python source (required).
        inputs: A mapping of templated values handed to the code as JSON;
            defaults to ``{"input": "{{ output }}"}``.
        timeout: Seconds the sandbox may run it (clamped by the sandbox's own
            ceiling).

    Raises:
        RuntimeError: Code nodes are disabled, or the code exited non-zero.
        ValueError: Invalid configuration or an oversized input.
    """
    from baselith.core.config.workflows import get_workflows_config

    if not get_workflows_config().code_enabled:
        raise RuntimeError(
            f"CODE node {node.id!r}: code nodes are disabled "
            "(set WORKFLOWS_CODE_ENABLED=true to run them in the sandbox)"
        )
    config = node.config
    code = config.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError(f"CODE node {node.id!r} needs a config['code']")
    if str(config.get("language", "python")).lower() != "python":
        raise ValueError(f"CODE node {node.id!r}: only python is supported")

    inputs = render_value(
        config.get("inputs", {"input": "{{ output }}"}), build_scope(context)
    )
    if not isinstance(inputs, dict):
        raise ValueError(f"CODE node {node.id!r}: inputs must be a mapping")
    payload = json.dumps(inputs, default=str)
    if len(payload.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError(
            f"CODE node {node.id!r}: inputs larger than {MAX_INPUT_BYTES} bytes"
        )

    timeout = config.get("timeout")
    runner = executor._services.get("code_runner") or _sandbox_runner
    result = await runner(
        code,
        envs={INPUT_ENV: payload},
        timeout=int(timeout) if timeout is not None else None,
    )
    if result.exit_code != 0:
        tail = (result.stderr or "").strip()[-_STDERR_TAIL:]
        raise RuntimeError(
            f"CODE node {node.id!r} exited with {result.exit_code}: {tail or 'no stderr'}"
        )
    text = (result.stdout or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        return text
