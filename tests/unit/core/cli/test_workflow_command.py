"""``baselith workflow``: validate, run, export, list and trigger helpers."""

import json

import pytest

from baselith.core.cli.commands.workflow import run_workflow
from baselith.core.webhooks.signing import verify_signature
from baselith.core.workflows.builder import WorkflowBuilder, WorkflowDefinition
from baselith.core.workflows.triggers import derive_workflow_secret

pytestmark = [pytest.mark.unit]


def _write(tmp_path, name="demo", template="hello {{ input }}", suffix=".yaml"):
    wf = WorkflowBuilder(name).start().template("t", template).end().build()
    wf.id = name
    path = tmp_path / f"{name}{suffix}"
    path.write_text(wf.to_json() if suffix == ".json" else wf.to_yaml())
    return path


def test_validate_reports_each_file_and_fails_on_any_invalid(tmp_path, capsys):
    good = _write(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text(WorkflowDefinition(name="b").to_yaml())
    assert run_workflow("validate", {"files": [str(good)]}) == 0
    assert run_workflow("validate", {"files": [str(good), str(bad)]}) == 1
    capsys.readouterr()
    run_workflow("validate", {"files": [str(good), str(bad)]}, json_output=True)
    results = json.loads(capsys.readouterr().out)["results"]
    assert [r["valid"] for r in results] == [True, False]
    assert any("START" in e for e in results[1]["errors"])


def test_validate_unreadable_file_is_a_failure_not_a_crash(tmp_path):
    assert run_workflow("validate", {"files": [str(tmp_path / "missing.yaml")]}) == 1


def test_run_prints_the_output(tmp_path, capsys):
    path = _write(tmp_path)
    code = run_workflow("run", {"file": str(path), "input": '"Ada"'})
    assert code == 0 and capsys.readouterr().out.strip() == "hello Ada"


def test_run_input_may_be_plain_text_or_a_file(tmp_path, capsys):
    path = _write(tmp_path, template="{{ input }}!")
    assert run_workflow("run", {"file": str(path), "input": "plain words"}) == 0
    assert capsys.readouterr().out.strip() == "plain words!"
    data = tmp_path / "in.txt"
    data.write_text("from file")
    assert run_workflow("run", {"file": str(path), "input_file": str(data)}) == 0
    assert capsys.readouterr().out.strip() == "from file!"


def test_run_json_output_carries_status_and_timing(tmp_path, capsys):
    path = _write(tmp_path)
    assert (
        run_workflow("run", {"file": str(path), "input": '"x"'}, json_output=True) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "completed" and payload["output"] == "hello x"
    assert payload["duration_ms"] >= 0


def test_run_failing_workflow_exits_nonzero(tmp_path):
    path = _write(tmp_path, template="{{ nodes.ghost }}")
    assert run_workflow("run", {"file": str(path)}) == 1


def test_run_refuses_an_invalid_definition(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(WorkflowDefinition(name="b").to_yaml())
    assert run_workflow("run", {"file": str(bad)}) == 1


def test_run_human_gate_fails_closed_without_a_checkpoint(tmp_path):
    wf = WorkflowBuilder("h").start().human("gate").end().build()
    path = tmp_path / "h.yaml"
    path.write_text(wf.to_yaml())
    assert run_workflow("run", {"file": str(path)}) == 1


def test_export_converts_between_formats(tmp_path, capsys):
    src = _write(tmp_path, suffix=".json")
    assert run_workflow("export", {"file": str(src), "to": "yaml"}) == 0
    assert capsys.readouterr().out.startswith("id: demo")
    out = tmp_path / "out.json"
    assert (
        run_workflow("export", {"file": str(src), "to": "json", "output": str(out)})
        == 0
    )
    assert json.loads(out.read_text())["id"] == "demo"


def test_list_shows_definitions_and_flags_rejected_files(tmp_path, capsys):
    _write(tmp_path, "one")
    (tmp_path / "junk.yaml").write_text("- nope\n")
    code = run_workflow("list", {"dir": str(tmp_path)}, json_output=True)
    payload = json.loads(capsys.readouterr().out)
    assert [w["id"] for w in payload["workflows"]] == ["one"]
    assert "junk.yaml" in payload["failed"]
    assert code == 1


def test_list_without_a_directory_explains_itself(monkeypatch, capsys):
    monkeypatch.delenv("WORKFLOWS_DIR", raising=False)
    monkeypatch.setattr(
        "baselith.core.config.workflows.get_workflows_config",
        lambda: type("C", (), {"directory": None})(),
    )
    assert run_workflow("list", {}) == 1


class TestTriggerHelpers:
    @pytest.fixture(autouse=True)
    def _master(self, monkeypatch):
        from baselith.core.config.workflows import WorkflowsConfig

        monkeypatch.setattr(
            "baselith.core.config.workflows.get_workflows_config",
            lambda: WorkflowsConfig(WORKFLOWS_TRIGGER_SECRET="master"),
        )

    def test_trigger_secret_prints_only_the_derived_key(self, capsys):
        assert run_workflow("trigger-secret", {"workflow_id": "demo"}) == 0
        out = capsys.readouterr().out.strip()
        assert out == derive_workflow_secret("master", "demo")
        assert "master" not in out

    def test_trigger_sign_produces_a_header_the_verifier_accepts(
        self, tmp_path, capsys
    ):
        body = tmp_path / "body.json"
        body.write_bytes(b'{"a": 1}')
        assert (
            run_workflow(
                "trigger-sign", {"workflow_id": "demo", "body_file": str(body)}
            )
            == 0
        )
        line = capsys.readouterr().out.strip()
        name, _, value = line.partition(": ")
        assert name == "X-Baselith-Signature"
        assert verify_signature(
            derive_workflow_secret("master", "demo"),
            b'{"a": 1}',
            value,
            tolerance_seconds=60,
        )

    def test_without_a_master_secret_both_fail(self, monkeypatch):
        from baselith.core.config.workflows import WorkflowsConfig

        monkeypatch.setattr(
            "baselith.core.config.workflows.get_workflows_config",
            lambda: WorkflowsConfig(),
        )
        assert run_workflow("trigger-secret", {"workflow_id": "x"}) == 1
        assert run_workflow("trigger-sign", {"workflow_id": "x", "body_file": "-"}) == 1


def test_unknown_subcommand():
    assert run_workflow("nope", {}) == 1


class TestGenerate:
    @staticmethod
    def _patch(monkeypatch, *, error=None):
        from baselith.core.workflows import generator

        seen = {}

        async def fake(description, *, existing=None, max_attempts=3):
            seen.update(
                description=description, existing=existing, attempts=max_attempts
            )
            if error:
                raise error
            wf = WorkflowBuilder("drafted").start().template("t", "hi").end().build()
            wf.id = "drafted"
            return generator.GenerationResult(wf, wf.to_yaml(), 2, ["assigned an id"])

        monkeypatch.setattr(generator, "generate_workflow", fake)
        return seen

    def test_prints_the_yaml(self, monkeypatch, capsys):
        seen = self._patch(monkeypatch)
        assert run_workflow("generate", {"description": "say hi"}) == 0
        out = capsys.readouterr().out
        assert (
            "id: drafted" in out and seen["attempts"] == 3 and seen["existing"] is None
        )

    def test_warnings_go_to_stderr_so_redirected_yaml_stays_valid(
        self, monkeypatch, capsys
    ):
        self._patch(monkeypatch)
        run_workflow("generate", {"description": "x"})
        captured = capsys.readouterr()
        assert "warning: assigned an id" in captured.err
        assert "warning" not in captured.out
        assert WorkflowDefinition.from_yaml(captured.out).id == "drafted"

    def test_writes_a_file(self, monkeypatch, tmp_path, capsys):
        self._patch(monkeypatch)
        target = tmp_path / "w.yaml"
        assert (
            run_workflow(
                "generate", {"description": "x", "output": str(target), "attempts": 2}
            )
            == 0
        )
        assert "id: drafted" in target.read_text()
        assert run_workflow("validate", {"files": [str(target)]}) == 0

    def test_revise_loads_the_file_first(self, monkeypatch, tmp_path):
        seen = self._patch(monkeypatch)
        path = _write(tmp_path, "current")
        assert run_workflow("generate", {"description": "x", "revise": str(path)}) == 0
        assert seen["existing"].id == "current"
        assert (
            run_workflow(
                "generate", {"description": "x", "revise": str(tmp_path / "nope.yaml")}
            )
            == 1
        )

    def test_json_output(self, monkeypatch, capsys):
        self._patch(monkeypatch)
        assert run_workflow("generate", {"description": "x"}, json_output=True) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["attempts"] == 2 and payload["workflow"]["id"] == "drafted"
        assert payload["warnings"] == ["assigned an id"]

    def test_failures_exit_nonzero(self, monkeypatch):
        from baselith.core.workflows.generator import GenerationError

        self._patch(monkeypatch, error=GenerationError(["no END node"]))
        assert run_workflow("generate", {"description": "x"}) == 1
        self._patch(monkeypatch, error=RuntimeError("provider down"))
        assert run_workflow("generate", {"description": "x"}) == 1
