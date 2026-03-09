"""
Unit Tests for Core Prioritization Module

Tests for task prioritization:
- Task and DependencyGraph
- TaskPrioritizer scoring
- PriorityQueue operations
"""

from datetime import datetime, timedelta

from core.prioritization import (
    Task,
    TaskStatus,
    DependencyGraph,
    TaskPrioritizer,
    PriorityQueue,
)


# ============================================================================
# Task Model Tests
# ============================================================================


class TestTask:
    """Tests for Task model."""

    def test_task_creation(self):
        """Basic task creation."""
        task = Task(id="t1", name="Test Task")

        assert task.id == "t1"
        assert task.status == TaskStatus.PENDING
        assert task.urgency == 0.5
        assert task.importance == 0.5

    def test_is_ready_no_dependencies(self):
        """Task with no deps is always ready."""
        task = Task(id="t1", name="Test")

        assert task.is_ready(set()) is True
        assert task.is_ready({"other"}) is True

    def test_is_ready_with_dependencies(self):
        """Task needs all deps completed."""
        task = Task(id="t1", name="Test", dependencies=["d1", "d2"])

        assert task.is_ready(set()) is False
        assert task.is_ready({"d1"}) is False
        assert task.is_ready({"d1", "d2"}) is True


# ============================================================================
# DependencyGraph Tests
# ============================================================================


class TestDependencyGraph:
    """Tests for DependencyGraph."""

    def test_add_and_get_task(self):
        """Add and retrieve tasks."""
        graph = DependencyGraph()
        task = Task(id="t1", name="Test")

        graph.add_task(task)

        assert graph.get_task("t1") == task
        assert len(graph) == 1

    def test_remove_task(self):
        """Remove task from graph."""
        graph = DependencyGraph()
        graph.add_task(Task(id="t1", name="Test"))

        removed = graph.remove_task("t1")

        assert removed is not None
        assert graph.get_task("t1") is None

    def test_get_dependents(self):
        """Track dependent tasks."""
        graph = DependencyGraph()
        graph.add_task(Task(id="t1", name="Task 1"))
        graph.add_task(Task(id="t2", name="Task 2", dependencies=["t1"]))
        graph.add_task(Task(id="t3", name="Task 3", dependencies=["t1"]))

        dependents = graph.get_dependents("t1")

        assert "t2" in dependents
        assert "t3" in dependents

    def test_mark_completed_unblocks_dependents(self):
        """Completing task unblocks dependents."""
        graph = DependencyGraph()
        graph.add_task(Task(id="t1", name="Task 1"))
        graph.add_task(Task(id="t2", name="Task 2", dependencies=["t1"]))

        newly_ready = graph.mark_completed("t1")

        assert "t2" in newly_ready
        assert graph.get_task("t1").status == TaskStatus.COMPLETED

    def test_has_cycle_no_cycle(self):
        """Detect no cycle in valid graph."""
        graph = DependencyGraph()
        graph.add_task(Task(id="t1", name="Task 1"))
        graph.add_task(Task(id="t2", name="Task 2", dependencies=["t1"]))

        assert graph.has_cycle() is False

    def test_topological_sort(self):
        """Topological ordering of tasks."""
        graph = DependencyGraph()
        graph.add_task(Task(id="t3", name="Task 3", dependencies=["t2"]))
        graph.add_task(Task(id="t2", name="Task 2", dependencies=["t1"]))
        graph.add_task(Task(id="t1", name="Task 1"))

        order = graph.topological_sort()

        # t1 should come before t2, t2 before t3
        assert order.index("t1") < order.index("t2")
        assert order.index("t2") < order.index("t3")


# ============================================================================
# TaskPrioritizer Tests
# ============================================================================


class TestTaskPrioritizer:
    """Tests for TaskPrioritizer."""

    def test_basic_scoring(self):
        """Basic priority scoring."""
        prioritizer = TaskPrioritizer()
        task = Task(id="t1", name="Test", urgency=0.8, importance=0.9)

        score = prioritizer.score(task)

        assert 0 <= score.total <= 1
        assert score.urgency_component > 0
        assert score.importance_component > 0

    def test_high_urgency_high_score(self):
        """Urgency increases score."""
        prioritizer = TaskPrioritizer()

        low_urgency = prioritizer.score(Task(id="t1", name="Low", urgency=0.2))
        high_urgency = prioritizer.score(Task(id="t2", name="High", urgency=0.9))

        assert high_urgency.total > low_urgency.total

    def test_low_effort_higher_priority(self):
        """Lower effort = higher priority (quick wins)."""
        prioritizer = TaskPrioritizer()

        low_effort = prioritizer.score(Task(id="t1", name="Easy", effort=0.1))
        high_effort = prioritizer.score(Task(id="t2", name="Hard", effort=0.9))

        assert low_effort.total > high_effort.total

    def test_deadline_urgency(self):
        """Approaching deadline increases score."""
        prioritizer = TaskPrioritizer()

        no_deadline = prioritizer.score(Task(id="t1", name="No deadline"))
        soon = prioritizer.score(
            Task(id="t2", name="Soon", deadline=datetime.now() + timedelta(days=1))
        )
        overdue = prioritizer.score(
            Task(id="t3", name="Overdue", deadline=datetime.now() - timedelta(days=1))
        )

        assert overdue.deadline_component > soon.deadline_component
        assert soon.deadline_component > no_deadline.deadline_component

    def test_dependency_boost(self):
        """Tasks with many dependents get priority boost."""
        prioritizer = TaskPrioritizer()
        task = Task(id="t1", name="Blocker", urgency=0.5, importance=0.5)

        no_deps = prioritizer.score(task, dependent_count=0)
        many_deps = prioritizer.score(task, dependent_count=5)

        assert many_deps.total > no_deps.total


# ============================================================================
# PriorityQueue Tests
# ============================================================================


class TestPriorityQueue:
    """Tests for PriorityQueue."""

    def test_enqueue_dequeue_order(self):
        """Higher priority dequeued first."""
        queue = PriorityQueue()

        queue.enqueue(Task(id="low", name="Low", urgency=0.2, importance=0.2))
        queue.enqueue(Task(id="high", name="High", urgency=0.9, importance=0.9))

        first = queue.dequeue()

        assert first.id == "high"

    def test_blocked_task_not_dequeued(self):
        """Blocked tasks not returned by dequeue."""
        queue = PriorityQueue()

        queue.enqueue(Task(id="t1", name="Blocked", dependencies=["missing"]))

        result = queue.dequeue()

        assert result is None

    def test_complete_unblocks_dependents(self):
        """Completing task unblocks dependent tasks."""
        queue = PriorityQueue()

        queue.enqueue(Task(id="t1", name="First"))
        queue.enqueue(Task(id="t2", name="Second", dependencies=["t1"]))

        # t2 should be blocked
        assert queue.graph.get_task("t2").status == TaskStatus.BLOCKED

        # Dequeue and complete t1
        task = queue.dequeue()
        assert task.id == "t1"

        newly_ready = queue.complete("t1")

        # t2 should now be ready
        assert len(newly_ready) == 1
        assert newly_ready[0].id == "t2"

    def test_reprioritize(self):
        """Reprioritization updates score."""
        queue = PriorityQueue()
        queue.enqueue(Task(id="t1", name="Test", urgency=0.5))

        old_score = queue.get_score("t1")
        queue.reprioritize("t1", urgency=0.9)
        new_score = queue.get_score("t1")

        assert new_score.total > old_score.total


# ============================================================================
# Integration Test
# ============================================================================


def test_priority_queue_workflow():
    """Full workflow: enqueue -> prioritize -> dequeue -> complete."""
    queue = PriorityQueue()

    # Add tasks with dependencies
    queue.enqueue(Task(id="setup", name="Setup", urgency=0.7))
    queue.enqueue(Task(id="build", name="Build", urgency=0.8, dependencies=["setup"]))
    queue.enqueue(Task(id="test", name="Test", urgency=0.6, dependencies=["build"]))
    queue.enqueue(Task(id="deploy", name="Deploy", urgency=0.9, dependencies=["test"]))

    # Only setup should be ready
    task1 = queue.dequeue()
    assert task1.id == "setup"

    # Complete setup, build becomes ready
    ready = queue.complete("setup")
    assert any(t.id == "build" for t in ready)

    # Continue chain
    task2 = queue.dequeue()
    assert task2.id == "build"

    queue.complete("build")
    task3 = queue.dequeue()
    assert task3.id == "test"

    queue.complete("test")
    task4 = queue.dequeue()
    assert task4.id == "deploy"


class TestTaskPrioritizerConfig:
    """Tests for TaskPrioritizer configuration injection."""

    def test_config_injection(self):
        """Test leveraging PrioritizationConfig."""
        from core.config.prioritization import PrioritizationConfig

        config = PrioritizationConfig(
            WEIGHT_URGENCY=0.5,
            WEIGHT_IMPORTANCE=0.1,
            WEIGHT_EFFORT=0.1,
            WEIGHT_DEADLINE=0.1,
            WEIGHT_DEPENDENCIES=0.2,
        )
        prioritizer = TaskPrioritizer(config=config)

        assert prioritizer.weight_urgency == 0.5
        assert prioritizer.weight_importance == 0.1

        # Verify score reflects weights
        # Task with max urgency
        task = Task(
            id="t1",
            name="Config Test",
            urgency=1.0,
            importance=0.0,
            effort=1.0,
            deadline=None,
        )

        # calculate expected score
        # urgency: 1.0 * 0.5 = 0.5
        # importance: 0.0 * 0.1 = 0.0
        # effort: 0.0 (1-1) * 0.1 = 0.0
        # deadline: 0.5 (neutral) * 0.1 = 0.05
        # dependency: 0.0 * 0.2 = 0.0
        # Total: 0.55

        score = prioritizer.score(task)
        assert score.urgency_component == 0.5
        assert abs(score.total - 0.55) < 0.001

    def test_legacy_overrides(self):
        """Test legacy arguments override config defaults."""
        prioritizer = TaskPrioritizer(weight_urgency=0.99)
        assert prioritizer.weight_urgency == 0.99
