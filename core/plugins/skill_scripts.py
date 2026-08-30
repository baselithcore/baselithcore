"""
Sandboxed execution of scripts bundled with declarative skills.

A ``SKILL.md`` skill may ship helper scripts under its ``scripts/``
subdirectory (enumerated on :class:`core.plugins.declarative.LoadedSkill`).
:func:`run_skill_script` executes one of them non-interactively: the path is
re-validated strictly inside the activated skill's ``scripts/`` directory
(traversal, absolute paths and symlink escapes are rejected), only ``.py``
scripts run (via ``sys.executable``), stdin is closed, both output streams
are capped, and the process is killed on timeout. JSON printed to stdout is
parsed into ``SkillScriptResult.parsed_json`` for structured consumption.

"Model proposes, code disposes": the model only ever names a skill and a
relative script filename — resolution and validation happen here.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from core.observability.logging import get_logger

from .declarative import DeclarativeSkillLoader, SkillLoadError, SkillSandboxError

if TYPE_CHECKING:
    from core.reasoning.react_types import ToolDefinition

logger = get_logger(__name__)

RUN_SKILL_SCRIPT_TOOL_NAME: Final[str] = "run_skill_script"
RUN_SKILL_SCRIPT_TOOL_DESCRIPTION: Final[str] = (
    "Run a Python helper script bundled with an activated skill (listed in "
    "the skill's scripts/). Non-interactive; stdout/stderr are captured. "
    'Usage: run_skill_script(skill="<skill name>", script="<file.py>", '
    'args=["..."]).'
)

#: Directory inside a skill that may contain executable helper scripts.
SCRIPTS_DIR_NAME: Final[str] = "scripts"
#: Cap applied independently to captured stdout and stderr.
MAX_OUTPUT_CHARS: Final[int] = 64 * 1024
TRUNCATION_MARKER: Final[str] = "\n[output truncated at 64KB]"
DEFAULT_TIMEOUT_S: Final[float] = 30.0


@dataclass(frozen=True)
class SkillScriptResult:
    """Outcome of one bundled-script execution.

    Attributes:
        exit_code: Process exit status (negative signal number when killed,
            e.g. after a timeout).
        stdout: Captured standard output, capped at 64KB with a marker.
        stderr: Captured standard error, capped at 64KB; carries a timeout
            marker when the script was killed.
        parsed_json: ``json.loads`` of stdout when it is valid JSON, else
            None.
    """

    exit_code: int
    stdout: str
    stderr: str
    parsed_json: Any | None = None


def _cap_output(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + TRUNCATION_MARKER
    return text


def _resolve_script_path(
    loader: DeclarativeSkillLoader, skill_name: str, script: str
) -> Path:
    """Resolve ``script`` strictly inside the skill's ``scripts/`` dir.

    Raises:
        ValueError: Unknown skill, non-``.py`` extension, or missing file.
        SkillSandboxError: Absolute path, traversal, or symlink escape.
    """
    card = next((c for c in loader.discover() if c.name == skill_name), None)
    if card is None:
        raise ValueError(f"Unknown skill '{skill_name}'")
    scripts_dir = (card.path.parent / SCRIPTS_DIR_NAME).resolve()
    requested = Path(script)
    if requested.is_absolute():
        raise SkillSandboxError(
            f"script path must be relative to the skill's {SCRIPTS_DIR_NAME}/ "
            f"directory, got absolute path {script!r}"
        )
    candidate = (scripts_dir / requested).resolve()
    try:
        candidate.relative_to(scripts_dir)
    except ValueError:
        raise SkillSandboxError(
            f"script {script!r} escapes the skill's {SCRIPTS_DIR_NAME}/ directory"
        ) from None
    if requested.suffix != ".py":
        raise ValueError(
            f"only .py scripts are supported, got {script!r} "
            f"(extension {requested.suffix or 'none'!r})"
        )
    if not candidate.is_file():
        raise ValueError(f"script {script!r} not found in skill '{skill_name}'")
    return candidate


async def run_skill_script(
    loader: DeclarativeSkillLoader,
    skill_name: str,
    script: str,
    args: list[str] | None = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> SkillScriptResult:
    """Execute a bundled skill script non-interactively.

    Args:
        loader: Loader whose roots contain the skill (sandbox authority).
        skill_name: Skill name as discovered in the catalog.
        script: Filename relative to the skill's ``scripts/`` directory.
        args: Extra argv passed to the script.
        timeout_s: Wall-clock budget; the process is killed when exceeded.

    Returns:
        :class:`SkillScriptResult` with capped output and, when stdout is
        valid JSON, the parsed payload.

    Raises:
        ValueError: Unknown skill, unsupported extension, missing script.
        SkillSandboxError: Path escapes the skill's ``scripts/`` directory.
    """
    path = _resolve_script_path(loader, skill_name, script)
    argv = [sys.executable, str(path), *(args or [])]
    logger.info(
        "skill_script_run", skill=skill_name, script=script, timeout_s=timeout_s
    )
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(path.parent.parent),  # the skill directory
    )
    timed_out = False
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        timed_out = True
        proc.kill()
        stdout_b, stderr_b = await proc.communicate()
    stdout = _cap_output(stdout_b)
    stderr = _cap_output(stderr_b)
    if timed_out:
        marker = f"[skill script killed after {timeout_s:g}s timeout]"
        stderr = f"{stderr}\n{marker}" if stderr else marker
    parsed: Any | None = None
    if not timed_out and stdout.strip():
        try:
            parsed = json.loads(stdout)
        except ValueError:
            parsed = None
    exit_code = proc.returncode if proc.returncode is not None else -1
    return SkillScriptResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr, parsed_json=parsed
    )


def make_run_skill_script_tool(loader: DeclarativeSkillLoader) -> ToolDefinition:
    """Build the ``run_skill_script`` ToolDefinition for tool registries.

    Mirrors :func:`core.plugins.skills_service.make_activation_tool_fn`:
    the callable returns plain text (tool observations are text) — a JSON
    report of the execution, or an actionable ``Error:`` string. Registered
    with autonomy category ``mutating``: bundled scripts execute code, so
    they are gated accordingly.
    """
    # Lazy import: core.reasoning pulls the whole reasoning stack, which
    # this module must not load at import time.
    from core.reasoning.react import ToolDefinition

    async def run_skill_script_tool(
        skill: str = "", script: str = "", args: list[str] | None = None
    ) -> str:
        if not skill.strip() or not script.strip():
            return (
                f"Error: {RUN_SKILL_SCRIPT_TOOL_NAME} requires a skill name "
                f"and a script filename, e.g. {RUN_SKILL_SCRIPT_TOOL_NAME}"
                '(skill="code-review", script="lint.py").'
            )
        try:
            result = await run_skill_script(
                loader, skill.strip(), script.strip(), list(args or [])
            )
        except (ValueError, SkillLoadError, SkillSandboxError) as exc:
            return f"Error: {exc}"
        return json.dumps(
            {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "parsed_json": result.parsed_json,
            },
            ensure_ascii=False,
        )

    return ToolDefinition(
        name=RUN_SKILL_SCRIPT_TOOL_NAME,
        fn=run_skill_script_tool,
        description=RUN_SKILL_SCRIPT_TOOL_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Skill name as listed in the catalog.",
                },
                "script": {
                    "type": "string",
                    "description": "Filename relative to the skill's scripts/.",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra command-line arguments.",
                },
            },
            "required": ["skill", "script"],
        },
        category="mutating",
    )


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MAX_OUTPUT_CHARS",
    "RUN_SKILL_SCRIPT_TOOL_DESCRIPTION",
    "RUN_SKILL_SCRIPT_TOOL_NAME",
    "SCRIPTS_DIR_NAME",
    "SkillScriptResult",
    "make_run_skill_script_tool",
    "run_skill_script",
]
