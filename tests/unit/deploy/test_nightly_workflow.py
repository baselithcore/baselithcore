"""The nightly workflow's restore drill, interpreter matrix and mutation run.

The nightly jobs only run on a schedule, so a drift between them and the files
they exercise (the Helm backup template, the restore script, the mutmut
scope) would surface a day late at best, and in the restore drill's case as a
red run nobody connects to the chart change that caused it. What can be read
off the files is pinned here, at commit time.
"""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
NIGHTLY = REPO_ROOT / ".github" / "workflows" / "nightly.yml"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HELPERS_TPL = (
    REPO_ROOT / "deploy" / "helm" / "baselithcore" / "templates" / "_helpers.tpl"
)
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "restore-db.sh"
DRILL_SCRIPT = REPO_ROOT / "scripts" / "backup_restore_drill.py"


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))["jobs"]


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def _drill_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("backup_restore_drill", DRILL_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_action_is_pinned_to_a_commit() -> None:
    uses = re.findall(
        r"^\s*-?\s*uses:\s*(\S+)", NIGHTLY.read_text(encoding="utf-8"), re.M
    )
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref


def test_forward_compat_covers_every_interpreter_past_the_floor() -> None:
    job = _jobs()["forward_compat"]
    assert job["strategy"]["matrix"]["python-version"] == ["3.13", "3.14"]
    assert job["strategy"]["fail-fast"] is False
    assert "continue-on-error" not in job


def test_restore_drill_runs_every_phase_against_the_ci_postgres() -> None:
    job = _jobs()["restore_drill"]
    runs = _runs(job)
    for phase in ("seed", "backup", "restore", "compare"):
        assert f"backup_restore_drill.py {phase}" in runs, phase
    assert 'verify-backup.sh "${BACKUP_FILE}"\n' in runs
    assert 'verify-backup.sh "${BACKUP_FILE}" --drill' in runs
    assert "core.db.migrate" in runs
    # The same pinned pgvector image as ci.yml's database jobs, digest and all.
    image = job["services"]["postgres"]["image"]
    assert "@sha256:" in image
    assert image in CI.read_text(encoding="utf-8")


def test_drill_knows_every_variable_the_chart_gives_the_dump() -> None:
    """A new variable in backupEnv changes what the CronJob's pg_dump sees;
    the drill must learn about it rather than dump with a different setup."""
    text = HELPERS_TPL.read_text(encoding="utf-8")
    block = text[text.index('define "baselithcore.backupEnv"') :]
    block = block[: block.index("{{- end -}}")]
    chart_env = set(re.findall(r"^- name: (\w+)", block, re.M))
    assert chart_env == set(_drill_module()._DUMP_ENV)


def test_restore_script_stops_on_the_first_error_in_one_transaction() -> None:
    script = RESTORE_SCRIPT.read_text(encoding="utf-8")
    assert "PSQL_RESTORE_FLAGS=(-v ON_ERROR_STOP=1 --single-transaction" in script
    psql_calls = re.findall(r"^(?!\s*#).*\bpsql\b.*$", script, re.M)
    assert psql_calls, "no psql invocation found"
    for call in psql_calls:
        assert '"${PSQL_RESTORE_FLAGS[@]}"' in call, call
    assert "--direct" in script


def test_mutation_testing_is_report_only_and_pinned() -> None:
    job = _jobs()["mutation_testing"]
    assert job["continue-on-error"] is True
    runs = _runs(job)
    assert re.search(r"mutmut==\d+\.\d+\.\d+", runs)
    uploads = [s for s in job["steps"] if "upload-artifact" in s.get("uses", "")]
    assert uploads and uploads[0].get("if") == "always()"


def test_mutation_scope_names_real_modules_and_tests() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mutmut = config["tool"]["mutmut"]
    assert mutmut["only_mutate"], "an empty only_mutate mutates all of core/"
    for module in mutmut["only_mutate"]:
        assert (REPO_ROOT / module).is_file(), module
    for selection in mutmut["pytest_add_cli_args_test_selection"]:
        assert (REPO_ROOT / selection).exists(), selection
    assert "--no-cov" in mutmut["pytest_add_cli_args"]
