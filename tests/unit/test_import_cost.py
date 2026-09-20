"""Import budgets: what touching one name is allowed to cost.

A package whose ``__init__`` imports every submodule makes the cost of any of
its names the cost of all of them. That is how ``from core.plugins.result
import SkillResult`` — four Pydantic fields — came to load 955 modules, and
how ``from core.agent import Agent`` came to load 3 209, including the
anthropic and openai SDKs whether or not either provider was configured.
:pep:`562` lazy resolution in :mod:`core._lazy` fixed both.

Nothing stops the next eager import from putting it back, and nobody notices
an import that got slower. These budgets are that alarm.

Module counts, not seconds: a count is the same on a laptop and on a CI
runner, while wall time is not. The budgets carry headroom so ordinary growth
does not flap them, and they are a **ratchet** — tighten one when the number
drops, never raise one to make a red test green. A jump past a budget means a
module-scope import landed somewhere it should have been deferred.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Packages no ordinary import path may pull in. The LLM SDKs are the ones
#: that mattered: 0.38 s and ~1 800 modules to import four provider clients so
#: that exactly one could be constructed.
FORBIDDEN = ("anthropic", "openai", "torch", "transformers", "playwright")

#: ``statement -> (module budget, measured at the time of writing)``.
BUDGETS: dict[str, tuple[int, int]] = {
    "import baselith": (150, 79),
    "from core.plugins import SkillResult": (350, 226),
    "from core.orchestration import AutonomyPolicy": (200, 95),
    "from core.agent import Agent": (1500, 1107),
}


def _measure(statement: str) -> tuple[int, list[str]]:
    """Run ``statement`` in a fresh interpreter; return modules and forbidden hits."""
    probe = (
        f"import sys\n{statement}\n"
        f"print(len(sys.modules))\n"
        f"print(' '.join(m for m in {FORBIDDEN!r} if m in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
        check=True,
    )
    count, forbidden = result.stdout.splitlines()[:2]
    return int(count), forbidden.split()


@pytest.mark.parametrize("statement", sorted(BUDGETS))
def test_import_stays_within_budget(statement: str) -> None:
    """One name must not cost the whole package."""
    budget, measured_at_writing = BUDGETS[statement]

    count, _ = _measure(statement)

    assert count <= budget, (
        f"`{statement}` now imports {count} modules, over its budget of "
        f"{budget} (it was {measured_at_writing} when the budget was set). "
        "Something gained a module-scope import that belongs inside the "
        "function or branch that needs it."
    )


@pytest.mark.parametrize("statement", sorted(BUDGETS))
def test_no_provider_sdk_is_imported(statement: str) -> None:
    """Configuring one provider must not cost importing all of them."""
    _, forbidden = _measure(statement)

    assert not forbidden, (
        f"`{statement}` imported {', '.join(forbidden)}. A provider SDK is "
        "imported inside the branch of core.services.llm.provider_factory "
        "that builds it, never at module scope."
    )


def test_importing_the_facade_touches_no_engine() -> None:
    """``import baselith`` resolves nothing; the first attribute read does."""
    probe = (
        "import sys, baselith\n"
        "print(sorted(m for m in sys.modules "
        "if m.startswith('core.') and m != 'core._version'))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
        check=True,
    )

    assert result.stdout.strip() == "[]"
