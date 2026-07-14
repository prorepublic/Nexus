"""Explicit, auditable state machines for goals, tasks, and runs.

Nexus deliberately uses a hand-written transition table rather than an agent
framework (ADR-005). Every transition is validated here; anything not listed
is an InvalidTransition error, which surfaces as a structured failure instead
of silent state corruption.
"""

from nexus.domain.enums import GoalStatus, RunStatus, TaskStatus


class InvalidTransition(Exception):
    def __init__(self, entity: str, current: str, target: str) -> None:
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"{entity}: illegal transition {current} -> {target}")


GOAL_TRANSITIONS: dict[GoalStatus, set[GoalStatus]] = {
    GoalStatus.DRAFT: {GoalStatus.PLANNING, GoalStatus.CANCELLED},
    GoalStatus.PLANNING: {
        GoalStatus.READY,
        GoalStatus.BLOCKED,
        GoalStatus.FAILED,
        GoalStatus.CANCELLED,
    },
    GoalStatus.READY: {GoalStatus.EXECUTING, GoalStatus.CANCELLED},
    GoalStatus.EXECUTING: {
        GoalStatus.BLOCKED,
        GoalStatus.REVIEW,
        GoalStatus.FAILED,
        GoalStatus.CANCELLED,
        GoalStatus.COMPLETED,
    },
    GoalStatus.BLOCKED: {
        GoalStatus.PLANNING,
        GoalStatus.EXECUTING,
        GoalStatus.FAILED,
        GoalStatus.CANCELLED,
    },
    GoalStatus.REVIEW: {
        GoalStatus.COMPLETED,
        GoalStatus.EXECUTING,
        GoalStatus.FAILED,
        GoalStatus.CANCELLED,
    },
    GoalStatus.COMPLETED: set(),
    GoalStatus.FAILED: {GoalStatus.PLANNING},  # a failed goal may be re-planned
    GoalStatus.CANCELLED: set(),
}

TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.READY: {TaskStatus.QUEUED, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.VALIDATING,
        TaskStatus.BLOCKED,  # mid-execution approval gate discovered
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.VALIDATING: {
        TaskStatus.REVIEW,
        TaskStatus.REPAIRING,
        TaskStatus.COMPLETED,
        TaskStatus.BLOCKED,  # untrusted-script approval gate
        TaskStatus.FAILED,
    },
    TaskStatus.REPAIRING: {
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.REVIEW: {
        TaskStatus.COMPLETED,
        TaskStatus.REPAIRING,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
    },
    TaskStatus.BLOCKED: {TaskStatus.READY, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: {TaskStatus.QUEUED},  # explicit operator retry only
    TaskStatus.CANCELLED: set(),
}

RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
    },
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
    RunStatus.TIMED_OUT: set(),
}

TERMINAL_TASK_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMED_OUT,
}


def assert_goal_transition(current: GoalStatus, target: GoalStatus) -> GoalStatus:
    if target not in GOAL_TRANSITIONS[current]:
        raise InvalidTransition("goal", current, target)
    return target


def assert_task_transition(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if target not in TASK_TRANSITIONS[current]:
        raise InvalidTransition("task", current, target)
    return target


def assert_run_transition(current: RunStatus, target: RunStatus) -> RunStatus:
    if target not in RUN_TRANSITIONS[current]:
        raise InvalidTransition("run", current, target)
    return target
