"""The coding agent's fix-rate benchmark: ``python -m plugins.coding_agent.evals``."""

from plugins.coding_agent.evals.cases import CASES, FixCase
from plugins.coding_agent.evals.runner import CaseResult, EvalReport, run_case, run_eval

__all__ = ["CASES", "CaseResult", "EvalReport", "FixCase", "run_case", "run_eval"]
