"""The coding agent's fix-rate benchmark: ``python -m baselith.plugins.coding_agent.evals``."""

from baselith.plugins.coding_agent.evals.cases import CASES, FixCase
from baselith.plugins.coding_agent.evals.runner import (
    CaseResult,
    EvalReport,
    run_case,
    run_eval,
    run_workspace_case,
    run_workspace_eval,
)
from baselith.plugins.coding_agent.evals.workspace_cases import (
    WORKSPACE_CASES,
    WorkspaceCase,
)

__all__ = [
    "CASES",
    "WORKSPACE_CASES",
    "CaseResult",
    "EvalReport",
    "FixCase",
    "WorkspaceCase",
    "run_case",
    "run_eval",
    "run_workspace_case",
    "run_workspace_eval",
]
