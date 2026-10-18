"""Coding Agent plugin package."""

from baselith.core.plugins.compat_aliases import register_module_aliases

from .agent import CodingAgent
from .plugin import CodingAgentPlugin
from .tools import build_coding_tool_definitions, register_coding_tools
from .types import (
    CodeExecutionResult,
    CodeLanguage,
    CodingResult,
    CodingTaskType,
)
from .workspace import Workspace, WorkspaceResult

__all__ = [
    "CodingAgent",
    "CodingAgentPlugin",
    "register_coding_tools",
    "build_coding_tool_definitions",
    "CodeExecutionResult",
    "CodeLanguage",
    "CodingResult",
    "CodingTaskType",
    "Workspace",
    "WorkspaceResult",
]

# The modules below moved into sub-packages (plugin layout standard); the old
# import paths stay importable so documented code and downstream plugins keep working.
register_module_aliases(
    {
        "durable": "runner.durable",
        "flows": "runner.flows",
        "harness": "runner.harness",
        "loop": "runner.loop",
        "screening": "runner.screening",
        "workspace_agent": "workspace.agent",
        "workspace_flow": "workspace.flow",
        "workspace_harness": "workspace.harness",
        "workspace_prompts": "workspace.prompts",
    },
    packages=(__name__,),
)
