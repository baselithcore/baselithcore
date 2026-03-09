import pytest
from datetime import timezone
from core.goals.tracker import Goal, GoalStatus, GoalTracker


class TestGoal:
    def test_goal_initialization(self):
        """Test basic goal initialization."""
        goal = Goal(id="1", title="Test Goal")
        assert goal.id == "1"
        assert goal.title == "Test Goal"
        assert goal.status == GoalStatus.NOT_STARTED
        assert goal.progress == 0.0
        assert goal.created_at.tzinfo == timezone.utc

    def test_goal_criteria_initialization(self):
        """Test initialization of success criteria."""
        criteria = ["task1", "task2"]
        goal = Goal(id="1", title="Test Goal", success_criteria=criteria)

        assert goal.success_criteria == criteria
        assert "task1" in goal.criteria_met
        assert "task2" in goal.criteria_met
        assert not goal.criteria_met["task1"]
        assert not goal.criteria_met["task2"]

    def test_update_progress(self):
        """Test progress updates."""
        goal = Goal(id="1", title="Test Goal")

        goal.update_progress(0.5)
        assert goal.progress == 0.5
        assert goal.status == GoalStatus.IN_PROGRESS
        assert goal.started_at is not None
        assert goal.started_at.tzinfo == timezone.utc

        goal.update_progress(1.5)  # Should cap at 1.0
        assert goal.progress == 1.0

        goal.update_progress(-0.5)  # Should cap at 0.0
        assert goal.progress == 0.0

    def test_is_complete(self):
        """Test is_complete property."""
        goal = Goal(id="1", title="Test Goal")
        assert not goal.is_complete

        goal.status = GoalStatus.COMPLETED
        assert goal.is_complete


class TestGoalTracker:
    @pytest.fixture
    def tracker(self):
        return GoalTracker()

    @pytest.mark.asyncio
    async def test_add_goal(self, tracker):
        """Test adding a goal."""
        goal = Goal(id="1", title="Test Goal")
        await tracker.add(goal)

        retrieved = tracker.get("1")
        assert retrieved == goal
        assert tracker.get("999") is None

    @pytest.mark.asyncio
    async def test_update_progress(self, tracker):
        """Test updating progress."""
        goal = Goal(id="1", title="Test Goal")
        await tracker.add(goal)

        success = await tracker.update_progress("1", 0.5)
        assert success
        assert tracker.get("1").progress == 0.5

        success = await tracker.update_progress("999", 0.5)
        assert not success

    @pytest.mark.asyncio
    async def test_complete_goal(self, tracker):
        """Test completing a goal."""
        goal = Goal(id="1", title="Test Goal")
        await tracker.add(goal)

        success = await tracker.complete("1")
        assert success

        completed_goal = tracker.get("1")
        assert completed_goal.status == GoalStatus.COMPLETED
        assert completed_goal.progress == 1.0
        assert completed_goal.completed_at is not None
        assert completed_goal.completed_at.tzinfo == timezone.utc

        success = await tracker.complete("999")
        assert not success

    @pytest.mark.asyncio
    async def test_fail_goal(self, tracker):
        """Test failing a goal."""
        goal = Goal(id="1", title="Test Goal")
        await tracker.add(goal)

        success = await tracker.fail("1", reason="Too hard")
        assert success

        failed_goal = tracker.get("1")
        assert failed_goal.status == GoalStatus.FAILED
        assert failed_goal.metadata["failure_reason"] == "Too hard"

        success = await tracker.fail("999")
        assert not success

    @pytest.mark.asyncio
    async def test_validate_criteria(self, tracker):
        """Test criteria validation."""
        goal = Goal(id="1", title="Test Goal", success_criteria=["c1", "c2"])
        await tracker.add(goal)

        # Initially false
        assert not tracker.validate_criteria("1")

        # Partially met
        goal.criteria_met["c1"] = True
        assert not tracker.validate_criteria("1")

        # Fully met
        goal.criteria_met["c2"] = True
        assert tracker.validate_criteria("1")

        # Goal without criteria
        simple_goal = Goal(id="2", title="Simple")
        await tracker.add(simple_goal)
        assert not tracker.validate_criteria("2")

    @pytest.mark.asyncio
    async def test_get_active_and_summary(self, tracker):
        """Test getting active goals and summary."""
        g1 = Goal(id="1", title="G1")  # Not started
        g2 = Goal(id="2", title="G2", status=GoalStatus.IN_PROGRESS)
        g3 = Goal(id="3", title="G3", status=GoalStatus.COMPLETED)
        g4 = Goal(id="4", title="G4", status=GoalStatus.FAILED)

        await tracker.add(g1)
        await tracker.add(g2)
        await tracker.add(g3)
        await tracker.add(g4)

        active = tracker.get_active()
        assert len(active) == 1
        assert active[0].id == "2"

        summary = tracker.get_summary()
        assert summary["total"] == 4
        assert summary["completed"] == 1
        assert summary["in_progress"] == 1
        assert summary["failed"] == 1
