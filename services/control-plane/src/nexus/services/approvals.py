"""Approval gates that actually control execution (ADR-009).

An approval is created by the engine when a gated action is needed, blocks the
owning task (or goal), and deterministically resumes exactly the blocked
operation when approved. Denial cancels the gated work and is never re-asked
for the same operation.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus.db.models import Approval, Goal, Task, utcnow
from nexus.domain.enums import ApprovalState, GoalStatus, Risk, TaskStatus
from nexus.domain.transitions import assert_goal_transition, assert_task_transition
from nexus.observability import get_logger
from nexus.services.events import record_audit

log = get_logger(__name__)


class ApprovalPending(Exception):
    """Raised by the engine when execution must stop and wait for the owner."""

    def __init__(self, approval: Approval) -> None:
        self.approval = approval
        super().__init__(f"approval pending: {approval.kind} ({approval.id})")


def find_decision(
    session: Session, kind: str, *, task_id: str | None = None, goal_id: str | None = None
) -> Approval | None:
    """Most recent approval of this kind for this task/goal, decided or not."""
    stmt = select(Approval).where(Approval.kind == kind)
    if task_id is not None:
        stmt = stmt.where(Approval.task_id == task_id)
    if goal_id is not None:
        stmt = stmt.where(Approval.goal_id == goal_id)
    return session.scalars(stmt.order_by(Approval.created_at.desc())).first()


def ensure_approval(
    session: Session,
    kind: str,
    description: str,
    *,
    risk: Risk = Risk.MEDIUM,
    goal_id: str | None = None,
    task_id: str | None = None,
) -> Approval:
    """Idempotently create (or return the existing) approval for an operation.

    Raises ApprovalPending unless an APPROVED decision already exists.
    A DENIED decision raises ApprovalPending too — the engine translates it
    into cancellation rather than asking again.
    """
    existing = find_decision(session, kind, task_id=task_id, goal_id=goal_id)
    if existing is not None:
        if existing.state == ApprovalState.APPROVED:
            return existing
        raise ApprovalPending(existing)
    approval = Approval(
        kind=kind, description=description[:4000], risk=risk, goal_id=goal_id, task_id=task_id
    )
    session.add(approval)
    session.flush()
    record_audit(
        session,
        "approval.requested",
        goal_id=goal_id,
        task_id=task_id,
        status="pending",
        metadata={"kind": kind, "approval_id": approval.id, "risk": str(risk)},
    )
    log.info("approval.requested", kind=kind, approval_id=approval.id)
    raise ApprovalPending(approval)


def decide_approval(
    session: Session, approval_id: str, decision: str, note: str | None = None
) -> Approval:
    """Record the owner's decision and resume or cancel the blocked work."""
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise ValueError("approval not found")
    if approval.state != ApprovalState.PENDING:
        raise ValueError(f"approval already {approval.state}")
    approval.state = ApprovalState.APPROVED if decision == "approved" else ApprovalState.DENIED
    approval.decided_at = utcnow()
    approval.decision_note = note
    record_audit(
        session,
        "approval.decided",
        actor="owner",
        goal_id=approval.goal_id,
        task_id=approval.task_id,
        status=str(approval.state),
        metadata={"kind": approval.kind, "approval_id": approval.id},
    )

    task = session.get(Task, approval.task_id) if approval.task_id else None
    goal = session.get(Goal, approval.goal_id) if approval.goal_id else None

    if approval.state == ApprovalState.APPROVED:
        if task is not None and TaskStatus(task.status) == TaskStatus.BLOCKED:
            task.status = assert_task_transition(TaskStatus.BLOCKED, TaskStatus.READY)
        if goal is not None and GoalStatus(goal.status) == GoalStatus.BLOCKED:
            goal.status = assert_goal_transition(GoalStatus.BLOCKED, GoalStatus.EXECUTING)
    else:
        if task is not None and TaskStatus(task.status) in {
            TaskStatus.BLOCKED,
            TaskStatus.PENDING,
            TaskStatus.READY,
            TaskStatus.QUEUED,
        }:
            task.status = TaskStatus.CANCELLED
            record_audit(
                session,
                "task.cancelled-by-denial",
                task_id=task.id,
                metadata={"approval_id": approval.id},
            )
        if goal is not None and approval.kind == "approve-plan":
            if GoalStatus(goal.status) in {GoalStatus.READY, GoalStatus.BLOCKED}:
                goal.status = GoalStatus.CANCELLED
                record_audit(session, "goal.cancelled-by-denial", goal_id=goal.id)
    return approval


def plan_approved(session: Session, goal: Goal) -> bool:
    """Manual-autonomy goals require an approved 'approve-plan' gate before any
    task is enqueued."""
    if goal.autonomy != "manual":
        return True
    decision = find_decision(session, "approve-plan", goal_id=goal.id)
    return decision is not None and decision.state == ApprovalState.APPROVED
