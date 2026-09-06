"""A run stays bound to the definition it started on.

The failure being guarded against is silent: a resumed run replays recorded
node outputs into an edited graph and takes a path it was never on. So the
tests are mostly about which edits count — a fingerprint that fires on a moved
canvas node gets switched off, and then it guards nothing.
"""

import pytest

from core.workflows.builder import (
    NodePosition,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from core.workflows.executor import ExecutionStatus, WorkflowExecutor
from core.workflows.versioning import (
    WORKFLOW_VERSION_KEY,
    VersionMismatchError,
    VersionPinning,
    WorkflowVersion,
    definition_fingerprint,
    pin_run_definition,
    pin_version,
    resolve_pinning_mode,
    verify_pinned_version,
)


def _workflow(**overrides) -> WorkflowDefinition:
    workflow = WorkflowDefinition(id="wf-1", name="test", **overrides)
    workflow.nodes = [
        WorkflowNode(id="start", type=NodeType.START, label="Start"),
        WorkflowNode(
            id="agent", type=NodeType.AGENT, label="Agent", agent_id="analyst"
        ),
        WorkflowNode(id="end", type=NodeType.END, label="End"),
    ]
    workflow.edges = [
        WorkflowEdge(id="e1", source_id="start", target_id="agent"),
        WorkflowEdge(id="e2", source_id="agent", target_id="end"),
    ]
    return workflow


class _Checkpoint:
    """Minimal stand-in for the checkpoint state the executor reaches into."""

    def __init__(self, plugin_data=None):
        self.checkpoint = type("_State", (), {"plugin_data": plugin_data or {}})()

    @property
    def plugin_data(self):
        return self.checkpoint.plugin_data


class TestFingerprint:
    def test_identical_definitions_agree(self):
        assert definition_fingerprint(_workflow()) == definition_fingerprint(
            _workflow()
        )

    def test_node_order_does_not_matter(self):
        """Storage order is not structure; a reordered list is the same graph."""
        shuffled = _workflow()
        shuffled.nodes.reverse()
        shuffled.edges.reverse()
        assert definition_fingerprint(shuffled) == definition_fingerprint(_workflow())

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(
                lambda w: w.nodes.append(
                    WorkflowNode(id="extra", type=NodeType.END, label="Extra")
                ),
                id="node added",
            ),
            pytest.param(
                lambda w: setattr(w.nodes[1], "agent_id", "other"), id="agent rebound"
            ),
            pytest.param(
                lambda w: setattr(w.nodes[1], "condition_expression", "x > 1"),
                id="condition changed",
            ),
            pytest.param(
                lambda w: setattr(w.nodes[1], "config", {"temperature": 0.9}),
                id="config changed",
            ),
            pytest.param(
                lambda w: setattr(w.nodes[1], "retries", 3), id="retry policy changed"
            ),
            pytest.param(
                lambda w: setattr(w.edges[0], "target_id", "end"), id="edge rerouted"
            ),
            pytest.param(
                lambda w: setattr(w.edges[0], "condition_label", "yes"),
                id="edge condition labelled",
            ),
        ],
    )
    def test_executable_changes_shift_the_fingerprint(self, mutate):
        changed = _workflow()
        mutate(changed)
        assert definition_fingerprint(changed) != definition_fingerprint(_workflow())

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda w: setattr(w.nodes[1], "label", "Renamed"), id="label"),
            pytest.param(
                lambda w: setattr(w.nodes[1], "position", NodePosition(x=99, y=99)),
                id="canvas position",
            ),
            pytest.param(lambda w: setattr(w, "name", "Other"), id="workflow name"),
            pytest.param(lambda w: setattr(w, "description", "docs"), id="description"),
            pytest.param(lambda w: w.metadata.update({"owner": "team"}), id="metadata"),
        ],
    )
    def test_cosmetic_changes_do_not(self, mutate):
        """Invalidating live runs over a renamed label would make this unusable."""
        changed = _workflow()
        mutate(changed)
        assert definition_fingerprint(changed) == definition_fingerprint(_workflow())

    def test_an_unserialisable_config_still_fingerprints(self):
        class Opaque:
            def __repr__(self) -> str:
                return "<opaque>"

        workflow = _workflow()
        workflow.nodes[1].config = {"handler": Opaque()}
        assert len(definition_fingerprint(workflow)) == 64


class TestPinRoundTrip:
    def test_a_pin_carries_the_declared_version_too(self):
        pinned = pin_version(_workflow(version="2.1.0"))
        assert pinned.version == "2.1.0"
        assert len(pinned.fingerprint) == 64

    def test_a_version_bump_alone_is_a_change(self):
        assert pin_version(_workflow(version="1.0.0")) != pin_version(
            _workflow(version="2.0.0")
        )

    def test_dict_round_trip(self):
        pinned = pin_version(_workflow())
        assert WorkflowVersion.from_dict(pinned.to_dict()) == pinned

    @pytest.mark.parametrize(
        "payload", [None, "nonsense", {}, {"version": 1, "fingerprint": "a"}]
    )
    def test_an_unreadable_pin_is_no_pin(self, payload):
        """A resume must never crash on a checkpoint it cannot parse."""
        assert WorkflowVersion.from_dict(payload) is None

    def test_describe_is_short_enough_for_a_log_line(self):
        assert "fingerprint=" in pin_version(_workflow()).describe()


class TestVerification:
    def test_an_unpinned_run_is_pinned_now(self):
        workflow = _workflow()
        assert verify_pinned_version(workflow, None) == pin_version(workflow)

    def test_an_unchanged_definition_keeps_its_pin(self):
        workflow = _workflow()
        pinned = pin_version(workflow)
        assert verify_pinned_version(workflow, pinned) is pinned

    def test_enforce_refuses_a_changed_definition(self):
        pinned = pin_version(_workflow())
        changed = _workflow()
        changed.nodes[1].agent_id = "other"

        with pytest.raises(VersionMismatchError) as excinfo:
            verify_pinned_version(changed, pinned, mode=VersionPinning.ENFORCE)
        assert "wf-1" in str(excinfo.value)
        assert excinfo.value.pinned == pinned

    def test_warn_continues_on_the_pinned_identity(self):
        pinned = pin_version(_workflow())
        changed = _workflow()
        changed.nodes[1].agent_id = "other"
        assert (
            verify_pinned_version(changed, pinned, mode=VersionPinning.WARN) is pinned
        )

    def test_off_ignores_the_mismatch_entirely(self):
        pinned = pin_version(_workflow())
        changed = _workflow()
        changed.nodes[1].agent_id = "other"
        assert verify_pinned_version(changed, pinned, mode=VersionPinning.OFF) is pinned


class TestMode:
    @pytest.mark.parametrize("value", ["off", "OFF", "false", "0", "disabled"])
    def test_off_spellings(self, value):
        assert resolve_pinning_mode(value) is VersionPinning.OFF

    @pytest.mark.parametrize("value", ["warn", " Warn ", "observe", "log"])
    def test_warn_spellings(self, value):
        assert resolve_pinning_mode(value) is VersionPinning.WARN

    @pytest.mark.parametrize("value", ["", "enforce", "typo", None])
    def test_anything_else_enforces(self, value, monkeypatch):
        """A typo must not silently disable the guard."""
        monkeypatch.delenv("BASELITH_WORKFLOW_VERSION_PINNING", raising=False)
        assert resolve_pinning_mode(value) is VersionPinning.ENFORCE

    def test_the_environment_is_read_when_no_value_is_given(self, monkeypatch):
        monkeypatch.setenv("BASELITH_WORKFLOW_VERSION_PINNING", "warn")
        assert resolve_pinning_mode() is VersionPinning.WARN


class TestCheckpointBinding:
    def test_a_run_without_a_checkpoint_is_not_pinned(self):
        """Nothing to resume means nothing a later deploy could change."""
        pin_run_definition(_workflow(), None)  # must not raise

    def test_the_first_pass_records_the_pin(self):
        checkpoint = _Checkpoint()
        workflow = _workflow()
        pin_run_definition(workflow, checkpoint)
        assert (
            checkpoint.plugin_data[WORKFLOW_VERSION_KEY]
            == pin_version(workflow).to_dict()
        )

    def test_a_resume_on_the_same_definition_is_silent(self):
        checkpoint = _Checkpoint()
        pin_run_definition(_workflow(), checkpoint)
        pin_run_definition(_workflow(), checkpoint)

    def test_a_resume_on_an_edited_definition_is_refused(self, monkeypatch):
        monkeypatch.delenv("BASELITH_WORKFLOW_VERSION_PINNING", raising=False)
        checkpoint = _Checkpoint()
        pin_run_definition(_workflow(), checkpoint)

        edited = _workflow()
        edited.nodes[1].agent_id = "impostor"
        with pytest.raises(VersionMismatchError):
            pin_run_definition(edited, checkpoint)

    def test_other_plugin_data_is_left_alone(self):
        checkpoint = _Checkpoint({"handler_state": {"n": 1}})
        pin_run_definition(_workflow(), checkpoint)
        assert checkpoint.plugin_data["handler_state"] == {"n": 1}


class _StubAgent:
    """Satisfies the AGENT node handler without reaching a model."""

    async def run(self, prompt):
        return "analysed"


def _executor() -> WorkflowExecutor:
    return WorkflowExecutor(agents={"analyst": _StubAgent()})


def _manager():
    """A real durable-run façade, so the executor takes its durable path."""
    from core.orchestration.checkpoint import (
        Checkpoint,
        CheckpointManager,
        InMemoryCheckpointStore,
    )

    return CheckpointManager(InMemoryCheckpointStore(), Checkpoint(run_id="run-1"))


class TestExecutorIntegration:
    async def test_an_edited_definition_fails_the_run_instead_of_replaying(
        self, monkeypatch
    ):
        monkeypatch.delenv("BASELITH_WORKFLOW_VERSION_PINNING", raising=False)
        checkpoint = _manager()
        executor = _executor()

        first = await executor.execute(_workflow(), checkpoint=checkpoint)
        assert first.status is ExecutionStatus.COMPLETED

        edited = _workflow()
        edited.nodes[1].agent_id = "impostor"
        second = await executor.execute(edited, checkpoint=checkpoint)

        assert second.status is ExecutionStatus.FAILED
        assert "changed while a run was in flight" in (second.error or "")

    async def test_a_run_without_a_checkpoint_is_unaffected(self):
        result = await _executor().execute(_workflow())
        assert result.status is ExecutionStatus.COMPLETED

    async def test_an_unchanged_definition_resumes_normally(self, monkeypatch):
        monkeypatch.delenv("BASELITH_WORKFLOW_VERSION_PINNING", raising=False)
        checkpoint = _manager()
        executor = _executor()
        await executor.execute(_workflow(), checkpoint=checkpoint)
        again = await executor.execute(_workflow(), checkpoint=checkpoint)
        assert again.status is ExecutionStatus.COMPLETED
