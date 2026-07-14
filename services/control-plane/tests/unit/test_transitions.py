import pytest

from nexus.domain.enums import GoalStatus, RunStatus, TaskStatus
from nexus.domain.transitions import (
    GOAL_TRANSITIONS,
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    InvalidTransition,
    assert_goal_transition,
    assert_run_transition,
    assert_task_transition,
)


class TestGoalTransitions:
    def test_happy_path(self):
        order = [
            GoalStatus.DRAFT,
            GoalStatus.PLANNING,
            GoalStatus.READY,
            GoalStatus.EXECUTING,
            GoalStatus.REVIEW,
            GoalStatus.COMPLETED,
        ]
        for current, target in zip(order, order[1:], strict=False):
            assert assert_goal_transition(current, target) == target

    def test_completed_is_terminal(self):
        for target in GoalStatus:
            with pytest.raises(InvalidTransition):
                assert_goal_transition(GoalStatus.COMPLETED, target)

    def test_cannot_skip_planning(self):
        with pytest.raises(InvalidTransition):
            assert_goal_transition(GoalStatus.DRAFT, GoalStatus.EXECUTING)

    def test_failed_goal_can_be_replanned(self):
        assert assert_goal_transition(GoalStatus.FAILED, GoalStatus.PLANNING)


class TestTaskTransitions:
    def test_execution_path(self):
        order = [
            TaskStatus.PENDING,
            TaskStatus.READY,
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.VALIDATING,
            TaskStatus.COMPLETED,
        ]
        for current, target in zip(order, order[1:], strict=False):
            assert assert_task_transition(current, target) == target

    def test_repair_path(self):
        assert assert_task_transition(TaskStatus.VALIDATING, TaskStatus.REPAIRING)
        assert assert_task_transition(TaskStatus.REPAIRING, TaskStatus.QUEUED)

    def test_cannot_complete_without_validation(self):
        with pytest.raises(InvalidTransition):
            assert_task_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED)

    def test_completed_is_terminal(self):
        for target in TaskStatus:
            with pytest.raises(InvalidTransition):
                assert_task_transition(TaskStatus.COMPLETED, target)

    def test_every_status_has_a_row(self):
        assert set(TASK_TRANSITIONS) == set(TaskStatus)
        assert set(GOAL_TRANSITIONS) == set(GoalStatus)
        assert set(RUN_TRANSITIONS) == set(RunStatus)


class TestRunTransitions:
    def test_run_lifecycle(self):
        assert assert_run_transition(RunStatus.QUEUED, RunStatus.RUNNING)
        assert assert_run_transition(RunStatus.RUNNING, RunStatus.SUCCEEDED)

    def test_terminal_runs_frozen(self):
        for terminal in (
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        ):
            for target in RunStatus:
                with pytest.raises(InvalidTransition):
                    assert_run_transition(terminal, target)
