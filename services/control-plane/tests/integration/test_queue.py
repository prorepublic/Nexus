import pytest

from nexus.db.models import Goal, Task, TaskDependency
from nexus.domain.enums import TaskStatus
from nexus.services.queue import claim_next_task, enqueue_ready_tasks

pytestmark = pytest.mark.integration


def make_goal_with_tasks(session, n=2, with_dependency=False):
    goal = Goal(title="g", description="d", status="ready")
    session.add(goal)
    session.flush()
    tasks = []
    for index in range(n):
        task = Task(goal_id=goal.id, title=f"t{index}", instruction="i", status=TaskStatus.PENDING)
        session.add(task)
        tasks.append(task)
    session.flush()
    if with_dependency and n >= 2:
        session.add(TaskDependency(task_id=tasks[1].id, depends_on_task_id=tasks[0].id))
    session.commit()
    return goal, tasks


class TestQueue:
    def test_enqueue_moves_pending_to_queued(self, db_session):
        _, tasks = make_goal_with_tasks(db_session)
        moved = enqueue_ready_tasks(db_session)
        db_session.commit()
        assert moved == 2
        for task in tasks:
            db_session.refresh(task)
            assert task.status == TaskStatus.QUEUED

    def test_dependency_blocks_enqueue(self, db_session):
        _, tasks = make_goal_with_tasks(db_session, with_dependency=True)
        enqueue_ready_tasks(db_session)
        db_session.commit()
        db_session.refresh(tasks[0])
        db_session.refresh(tasks[1])
        assert tasks[0].status == TaskStatus.QUEUED
        assert tasks[1].status == TaskStatus.PENDING

    def test_dependency_released_after_completion(self, db_session):
        _, tasks = make_goal_with_tasks(db_session, with_dependency=True)
        enqueue_ready_tasks(db_session)
        tasks[0].status = TaskStatus.COMPLETED  # simulate finished dependency
        db_session.commit()
        enqueue_ready_tasks(db_session)
        db_session.commit()
        db_session.refresh(tasks[1])
        assert tasks[1].status == TaskStatus.QUEUED

    def test_claim_flips_to_running_and_counts_attempt(self, db_session):
        make_goal_with_tasks(db_session, n=1)
        enqueue_ready_tasks(db_session)
        db_session.commit()
        task = claim_next_task(db_session)
        db_session.commit()
        assert task is not None
        assert task.status == TaskStatus.RUNNING
        assert task.attempt_count == 1

    def test_skip_locked_prevents_double_claim(self, db_session):
        """A second session must not claim the row locked by the first."""
        from nexus.db.base import get_session_factory

        make_goal_with_tasks(db_session, n=1)
        enqueue_ready_tasks(db_session)
        db_session.commit()

        first = get_session_factory()()
        second = get_session_factory()()
        try:
            claimed_first = claim_next_task(first)  # holds row lock, uncommitted
            claimed_second = claim_next_task(second)
            assert claimed_first is not None
            assert claimed_second is None  # SKIP LOCKED skipped the locked row
            first.commit()
        finally:
            first.close()
            second.rollback()
            second.close()

    def test_claim_order_is_fifo(self, db_session):
        _, tasks = make_goal_with_tasks(db_session, n=2)
        enqueue_ready_tasks(db_session)
        db_session.commit()
        first = claim_next_task(db_session)
        db_session.commit()
        assert first.title == "t0"
