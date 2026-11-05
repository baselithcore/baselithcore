"""Workflow catalog: validation on entry, directory persistence, safety."""

import pytest

from baselith.core.workflows.builder import WorkflowBuilder, WorkflowDefinition
from baselith.core.workflows.catalog import (
    CatalogError,
    WorkflowCatalog,
    is_valid_workflow_id,
)

pytestmark = [pytest.mark.unit]


def _wf(workflow_id: str = "greet", **fields) -> WorkflowDefinition:
    wf = WorkflowBuilder("g").start().template("t", "hi {{ input }}").end().build()
    wf.id = workflow_id
    for key, value in fields.items():
        setattr(wf, key, value)
    return wf


@pytest.mark.parametrize(
    ("workflow_id", "ok"),
    [
        ("greet", True),
        ("a.b-c_d", True),
        ("9lives", True),
        ("", False),
        (".hidden", False),
        ("-dash", False),
        ("a/b", False),
        ("..", False),
        ("a..b", False),
        ("x" * 101, False),
        ("sp ace", False),
        ("ünï", False),
    ],
)
def test_workflow_id_rules(workflow_id, ok):
    assert is_valid_workflow_id(workflow_id) is ok


class TestRegister:
    def test_register_get_list_remove(self):
        catalog = WorkflowCatalog()
        assert catalog.register(_wf("b")) is False  # memory-only: nothing written
        catalog.register(_wf("a"))
        assert [w.id for w in catalog.list()] == ["a", "b"]
        assert "a" in catalog and len(catalog) == 2
        assert catalog.remove("a") is True
        assert catalog.remove("a") is False
        assert catalog.get("a") is None

    def test_invalid_definition_never_enters(self):
        catalog = WorkflowCatalog()
        broken = WorkflowDefinition(id="broken", name="x")
        with pytest.raises(CatalogError, match="START"):
            catalog.register(broken)
        assert len(catalog) == 0

    def test_invalid_id_is_refused(self):
        with pytest.raises(CatalogError, match="invalid workflow id"):
            WorkflowCatalog().register(_wf("../etc/passwd"))

    def test_catalog_has_a_ceiling_but_replacement_is_free(self):
        catalog = WorkflowCatalog(max_workflows=2)
        catalog.register(_wf("a"))
        catalog.register(_wf("b"))
        catalog.register(_wf("a"))  # replace, not a third entry
        with pytest.raises(CatalogError, match="full"):
            catalog.register(_wf("c"))

    def test_listeners_hear_changes_and_cannot_break_them(self):
        seen = []
        catalog = WorkflowCatalog()
        catalog.subscribe(lambda e, i: seen.append((e, i)))
        catalog.subscribe(lambda e, i: 1 / 0)
        catalog.register(_wf("a"))
        catalog.remove("a")
        assert seen == [("registered", "a"), ("removed", "a")]
        assert len(catalog) == 0


class TestPersistence:
    def test_persist_writes_yaml_and_survives_reload(self, tmp_path):
        catalog = WorkflowCatalog(tmp_path)
        assert catalog.register(_wf("greet"), persist=True) is True
        assert (tmp_path / "greet.yaml").is_file()

        fresh = WorkflowCatalog(tmp_path)
        report = fresh.load_directory()
        assert report.loaded == ["greet"] and report.failed == {}
        assert fresh.get("greet").validate() == []

    def test_remove_deletes_the_file(self, tmp_path):
        catalog = WorkflowCatalog(tmp_path)
        catalog.register(_wf("greet"), persist=True)
        catalog.remove("greet")
        assert not (tmp_path / "greet.yaml").exists()

    def test_without_persist_flag_nothing_is_written(self, tmp_path):
        WorkflowCatalog(tmp_path).register(_wf("greet"))
        assert list(tmp_path.iterdir()) == []

    def test_replacing_a_json_file_definition_leaves_one_file(self, tmp_path):
        (tmp_path / "greet.json").write_text(_wf("greet").to_json())
        catalog = WorkflowCatalog(tmp_path)
        assert catalog.load_directory().loaded == ["greet"]
        catalog.register(_wf("greet", description="v2"), persist=True)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["greet.yaml"]

    def test_bad_files_are_reported_and_skipped(self, tmp_path):
        (tmp_path / "ok.yaml").write_text(_wf("ok").to_yaml())
        (tmp_path / "junk.yaml").write_text("- not\n- a mapping\n")
        (tmp_path / "broken.yaml").write_text(
            WorkflowDefinition(id="broken", name="b").to_yaml()
        )
        (tmp_path / "notes.txt").write_text("ignored")
        report = WorkflowCatalog(tmp_path).load_directory()
        assert report.loaded == ["ok"]
        assert set(report.failed) == {"junk.yaml", "broken.yaml"}

    def test_duplicate_ids_across_files_keep_the_first(self, tmp_path):
        (tmp_path / "a.yaml").write_text(_wf("same").to_yaml())
        (tmp_path / "b.yaml").write_text(_wf("same").to_yaml())
        catalog = WorkflowCatalog(tmp_path)
        report = catalog.load_directory()
        assert report.loaded == ["same"]
        assert "already defined" in report.failed["b.yaml"]

    def test_file_id_cannot_escape_the_directory(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        catalog = WorkflowCatalog(tmp_path / "wf")
        with pytest.raises(CatalogError):
            catalog.register(_wf("../outside/evil"), persist=True)
        assert list(outside.iterdir()) == []

    def test_missing_directory_loads_nothing(self, tmp_path):
        report = WorkflowCatalog(tmp_path / "nope").load_directory()
        assert report.loaded == [] and report.failed == {}
