"""PostgreSQL-backed work queue (ADR-003).

No Redis, no external broker: tasks are claimed with row locking and
FOR UPDATE SKIP LOCKED so multiple orchestrator workers can safely pull work
from the same table without double-claiming.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus.db.models import Task, utcnow
from nexus.domain.enums import TaskStatus
from nexus.domain.transitions import assert_task_transition


def enqueue_ready_tasks(session: Session, goal_id: str | None = None) -> int:
    """Move PENDING tasks whose dependencies are all satisfied to QUEUED."""
    from nexus.db.models import TaskDependency

    stmt = select(Task).where(Task.status.in_([TaskStatus.PENDING, TaskStatus.READY]))
    if goal_id:
        stmt = stmt.where(Task.goal_id == goal_id)
    moved = 0
    for task in session.scalars(stmt):
        dep_ids = session.scalars(
            select(TaskDependency.depends_on_task_id).where(TaskDependency.task_id == task.id)
        ).all()
        if dep_ids:
            dep_statuses = {
                TaskStatus(status)
                for status in session.scalars(select(Task.status).where(Task.id.in_(dep_ids)))
            }
            # A dependency that terminally failed or was cancelled can never be
            # satisfied: cancel the dependent so the goal can settle honestly.
            if dep_statuses & {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                task.status = assert_task_transition(TaskStatus(task.status), TaskStatus.CANCELLED)
                continue
            if dep_statuses != {TaskStatus.COMPLETED}:
                continue
        current = TaskStatus(task.status)
        if current == TaskStatus.PENDING:
            task.status = assert_task_transition(current, TaskStatus.READY)
            current = TaskStatus.READY
        task.status = assert_task_transition(current, TaskStatus.QUEUED)
        moved += 1
    return moved


def claim_next_task(session: Session) -> Task | None:
    """Atomically claim one queued task using SKIP LOCKED semantics.

    The caller owns the transaction: the row lock is held until commit, and the
    status flip to RUNNING inside the same transaction makes the claim durable.
    """
    stmt = (
        select(Task)
        .where(Task.status == TaskStatus.QUEUED)
        .order_by(Task.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    task = session.scalars(stmt).first()
    if task is None:
        return None
    task.status = assert_task_transition(TaskStatus(task.status), TaskStatus.RUNNING)
    task.started_at = utcnow()
    task.attempt_count += 1
    return task
