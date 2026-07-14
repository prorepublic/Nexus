"""PostgreSQL-backed work queue (ADR-003, hardened per ADR-010).

No Redis, no external broker: tasks are claimed with row locking and
FOR UPDATE SKIP LOCKED so multiple orchestrator workers can safely pull work
from the same table without double-claiming. Reliability features:

- plan-approval gating: a manual-autonomy goal's tasks are not enqueued until
  the owner approves the plan;
- dependents of terminally failed/cancelled dependencies are cancelled so the
  goal settles honestly;
- lease-based stale-run recovery: a run whose lease expired (its orchestrator
  died) is failed as infrastructure and its task requeued within the repair
  budget — a task is never executed twice concurrently.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus.config import get_settings
from nexus.db.models import Goal, Run, Task, utcnow
from nexus.domain.enums import GoalStatus, RunStatus, TaskStatus
from nexus.domain.transitions import assert_run_transition, assert_task_transition
from nexus.observability import get_logger
from nexus.services.events import record_audit

log = get_logger(__name__)


def enqueue_ready_tasks(session: Session, goal_id: str | None = None) -> int:
    """Move PENDING tasks whose dependencies are all satisfied to QUEUED."""
    from nexus.db.models import TaskDependency
    from nexus.services.approvals import plan_approved

    stmt = select(Task).where(Task.status.in_([TaskStatus.PENDING, TaskStatus.READY]))
    if goal_id:
        stmt = stmt.where(Task.goal_id == goal_id)
    moved = 0
    goal_gate_cache: dict[str, bool] = {}
    for task in session.scalars(stmt):
        allowed = goal_gate_cache.get(task.goal_id)
        if allowed is None:
            goal = session.get(Goal, task.goal_id)
            allowed = (
                goal is not None
                and GoalStatus(goal.status)
                in {GoalStatus.READY, GoalStatus.EXECUTING, GoalStatus.BLOCKED}
                and plan_approved(session, goal)
            )
            goal_gate_cache[task.goal_id] = allowed
        if not allowed:
            continue

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


def recover_stale_runs(session: Session) -> int:
    """Fail runs whose lease expired (their orchestrator process died) and
    requeue the task within its repair budget. Never lets a task run twice:
    recovery only touches runs past their lease, and claiming is transactional.
    """
    settings = get_settings()
    now = utcnow()
    stale_runs = session.scalars(
        select(Run)
        .where(Run.status == RunStatus.RUNNING, Run.lease_expires_at < now)
        .with_for_update(skip_locked=True)
    ).all()
    recovered = 0
    for run in stale_runs:
        run.status = assert_run_transition(RunStatus.RUNNING, RunStatus.FAILED)
        run.error_category = "infrastructure"
        run.exit_summary = "lease expired: orchestrator process died or hung"
        run.finished_at = now
        task = session.get(Task, run.task_id)
        if task is not None and TaskStatus(task.status) == TaskStatus.RUNNING:
            task.status = assert_task_transition(TaskStatus.RUNNING, TaskStatus.FAILED)
            budget = min(task.max_attempts, 1 + settings.max_repair_attempts)
            if task.attempt_count < budget:
                task.status = assert_task_transition(TaskStatus.FAILED, TaskStatus.QUEUED)
            record_audit(
                session,
                "run.recovered-stale",
                task_id=task.id,
                run_id=run.id,
                metadata={
                    "attempt": task.attempt_count,
                    "requeued": task.status == TaskStatus.QUEUED,
                },
            )
        recovered += 1
        log.info("queue.stale_run_recovered", run_id=run.id)
    return recovered
