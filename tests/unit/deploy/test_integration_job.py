"""The CI ``integration_test`` job runs every real-Postgres module.

The Postgres cases of these modules skip everywhere else (``tests/conftest.py``
mocks psycopg unless ``BASELITH_TEST_REAL_DB=1``), so a module missing from the
job's command line — or a job that lost its service or its flag — is a store
contract that silently stops being tested against the database it ships for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: Real-database modules outside ``tests/integration/`` the job must name.
OUTSIDE_INTEGRATION = (
    "tests/core/skill_evolution/test_store_postgres.py",
    "tests/unit/core/orchestration/test_checkpoint_store_contract.py",
    "tests/unit/core/orchestration/test_checkpoint_lease_clock.py",
)

#: Real-database modules the ``tests/integration`` argument already covers.
INSIDE_INTEGRATION = (
    "tests/integration/test_online_eval_store_postgres.py",
    "tests/integration/test_migration_upgrade.py",
)


def _job() -> dict[str, Any]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["integration_test"]


def _pytest_command(job: dict[str, Any]) -> str:
    runs = [s.get("run", "") for s in job["steps"] if "pytest" in s.get("run", "")]
    assert len(runs) == 1, runs
    return runs[0]


def test_job_has_a_postgres_service_and_the_real_db_opt_in() -> None:
    job = _job()
    assert "postgres" in job["services"]
    assert "pgvector/pgvector" in job["services"]["postgres"]["image"]
    assert job["env"]["BASELITH_TEST_REAL_DB"] == "1"
    assert job["env"]["BASELITH_TEST_INTEGRATION_STRICT"] == "1"


def test_job_applies_migrations_before_the_suite() -> None:
    steps = [s.get("run", "") for s in _job()["steps"]]
    migrate = next(i for i, r in enumerate(steps) if "baselith.core.db.migrate" in r)
    suite = next(i for i, r in enumerate(steps) if "pytest" in r)
    assert migrate < suite


def test_job_runs_every_real_database_module() -> None:
    command = _pytest_command(_job())
    assert "tests/integration" in command.split()
    for module in OUTSIDE_INTEGRATION + INSIDE_INTEGRATION:
        assert (REPO_ROOT / module).is_file(), module
    for module in OUTSIDE_INTEGRATION:
        assert module in command, module


def test_the_migration_module_covers_down_and_back_up() -> None:
    source = (REPO_ROOT / "tests/integration/test_migration_upgrade.py").read_text(
        encoding="utf-8"
    )
    assert "def test_downgrade_to_base_and_back_converges" in source
