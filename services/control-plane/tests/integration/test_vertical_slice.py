"""End-to-end orchestration through the fake worker: goal -> plan -> tasks ->
execute -> fail-closed validation -> independent review -> bounded repair ->
completion. Zero model usage."""

import pytest
from sqlalchemy import select

from nexus.db.models import AuditEvent, Goal, ReviewFinding, Run, Task
from nexus.domain.enums import GoalStatus, RunStatus, TaskStatus, WorkerName
from nexus.services.orchestrator import Orchestrator, cancel_run
from nexus.services.planner import DeterministicPlanner, create_plan
from nexus.workers.fake import FakeWorker
from nexus.workers.registry import WorkerRegistry

pytestmark = pytest.mark.integration


@pytest.fixture
def registry():
    return WorkerRegistry({WorkerName.FAKE: FakeWorker()})


def create_goal(session, description, criteria=None, title="Test goal", autonomy="bounded"):
    """Create and plan a fake-worker goal. Implementation tasks get files-exist
    evidence derived from the [fake:write=...] directive (validation fails
    closed, so evidence must be declared)."""
    import re

    goal = Goal(
        title=title,
        description=description,
        requested_worker="fake",
        acceptance_criteria=criteria or [],
        autonomy=autonomy,
    )
    session.add(goal)
    session.flush()
    create_plan(session, goal, DeterministicPlanner())
    write_target = re.search(r"\[fake:write=([^\]]+)\]", description)
    expected = [write_target.group(1) if write_target else "FAKE_WORKER_RESULT.md"]
    for task in goal.tasks:
        if task.kind == "implementation":
            task.expected_files = expected
            task.validation_spec = ["files-exist"]
    session.commit()
    return goal


def drain(orchestrator, max_ticks=30):
    for _ in range(max_ticks):
        if not orchestrator.tick():
            break


class TestVerticalSlice:
    def test_goal_to_completion(self, db_session, registry):
        goal = create_goal(db_session, "Do the thing. [fake:write=proof.md] [fake:review=approved]")
        assert goal.status == GoalStatus.READY

        drain(Orchestrator(registry=registry))

        db_session.expire_all()
        goal = db_session.get(Goal, goal.id)
        assert goal.status == GoalStatus.REVIEW  # ready for human review
        tasks = db_session.scalars(select(Task).where(Task.goal_id == goal.id)).all()
        assert tasks and all(t.status == TaskStatus.COMPLETED for t in tasks)
        impl = next(t for t in tasks if t.kind == "implementation")
        assert impl.review_verdict == "approved"

    def test_acceptance_criteria_become_dependent_tasks(self, db_session, registry):
        goal = create_goal(
            db_session,
            "Multi-step [fake:review=approved]",
            criteria=["first", "second"],
        )
        tasks = db_session.scalars(select(Task).where(Task.goal_id == goal.id)).all()
        assert len(tasks) == 3  # two implementation + one goal-level review

        drain(Orchestrator(registry=registry))
        db_session.expire_all()
        assert db_session.get(Goal, goal.id).status == GoalStatus.REVIEW

    def test_bounded_repair_recovers_from_transient_failure(self, db_session, registry):
        goal = create_goal(db_session, "Flaky work [fake:fail-first=1] [fake:review=approved]")
        drain(Orchestrator(registry=registry))

        db_session.expire_all()
        goal = db_session.get(Goal, goal.id)
        assert goal.status == GoalStatus.REVIEW
        impl = db_session.scalars(
            select(Task).where(Task.goal_id == goal.id, Task.kind == "implementation")
        ).one()
        assert impl.attempt_count == 2  # failed once, repaired once
        repair_events = db_session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "task.repair-scheduled")
        ).all()
        assert repair_events

    def test_repair_budget_is_finite(self, db_session, registry):
        goal = create_goal(db_session, "Hopeless work [fake:fail]")
        drain(Orchestrator(registry=registry))

        db_session.expire_all()
        goal = db_session.get(Goal, goal.id)
        assert goal.status == GoalStatus.FAILED
        impl = db_session.scalars(
            select(Task).where(Task.goal_id == goal.id, Task.kind == "implementation")
        ).one()
        assert impl.status == TaskStatus.FAILED
        # attempts bounded by 1 + max_repair_attempts (settings default: 2)
        assert impl.attempt_count == 3
        # review task was cancelled because its dependency terminally failed
        review = db_session.scalars(
            select(Task).where(Task.goal_id == goal.id, Task.kind == "review")
        ).one()
        assert review.status == TaskStatus.CANCELLED

    def test_review_rejection_drives_contextual_repair(self, db_session, registry):
        """The reviewer requests changes once; the repair attempt receives the
        findings in its instruction context and the task then completes."""
        goal = create_goal(
            db_session, "Reviewed work [fake:write=out.md] [fake:review=changes-once]"
        )
        drain(Orchestrator(registry=registry))

        db_session.expire_all()
        goal = db_session.get(Goal, goal.id)
        assert goal.status == GoalStatus.REVIEW
        impl = db_session.scalars(
            select(Task).where(Task.goal_id == goal.id, Task.kind == "implementation")
        ).one()
        assert impl.status == TaskStatus.COMPLETED
        assert impl.attempt_count == 2  # one repair driven by review findings
        assert impl.review_verdict == "approved"
        # findings persisted from the changes-requested review
        findings = db_session.scalars(
            select(ReviewFinding).where(ReviewFinding.task_id == impl.id)
        ).all()
        assert any(f.blocking for f in findings)
        # the repair context carried the findings into the second attempt
        assert (impl.context or {}).get("review_findings")
        # review runs exist alongside implementation runs
        purposes = {
            run.purpose for run in db_session.scalars(select(Run).where(Run.task_id == impl.id))
        }
        assert purposes == {"implementation", "review"}

    def test_validation_failure_fails_closed(self, db_session, registry):
        """A worker that succeeds but produces no declared evidence must not
        complete."""
        goal = create_goal(db_session, "Wrong output [fake:write=other.md]")
        impl = db_session.scalars(
            select(Task).where(Task.goal_id == goal.id, Task.kind == "implementation")
        ).one()
        impl.expected_files = ["the-required-file.md"]  # never produced
        db_session.commit()

        drain(Orchestrator(registry=registry))
        db_session.expire_all()
        impl = db_session.get(Task, impl.id)
        assert impl.status == TaskStatus.FAILED
        assert (impl.context or {}).get("failed_validations")

    def test_manual_autonomy_blocks_until_plan_approved(self, db_session, registry):
        from nexus.services.approvals import decide_approval, find_decision

        goal = create_goal(
            db_session,
            "Gated work [fake:write=gated.md] [fake:review=approved]",
            autonomy="manual",
        )
        orchestrator = Orchestrator(registry=registry)
        drain(orchestrator, max_ticks=3)
        db_session.expire_all()
        tasks = db_session.scalars(select(Task).where(Task.goal_id == goal.id)).all()
        assert all(t.status == TaskStatus.PENDING for t in tasks)  # nothing ran

        approval = find_decision(db_session, "approve-plan", goal_id=goal.id)
        assert approval is not None and approval.state == "pending"
        decide_approval(db_session, approval.id, "approved")
        db_session.commit()

        drain(orchestrator)
        db_session.expire_all()
        assert db_session.get(Goal, goal.id).status == GoalStatus.REVIEW

    def test_plan_denial_cancels_goal(self, db_session, registry):
        from nexus.services.approvals import decide_approval, find_decision

        goal = create_goal(db_session, "Denied work [fake:write=x.md]", autonomy="manual")
        approval = find_decision(db_session, "approve-plan", goal_id=goal.id)
        decide_approval(db_session, approval.id, "denied", note="not now")
        db_session.commit()
        db_session.expire_all()
        assert db_session.get(Goal, goal.id).status == GoalStatus.CANCELLED

    def test_audit_trail_exists(self, db_session, registry):
        create_goal(db_session, "Audited [fake:write=a.md] [fake:review=approved]")
        drain(Orchestrator(registry=registry))
        events = {e.event_type for e in db_session.scalars(select(AuditEvent))}
        assert {
            "run.started",
            "task.routed",
            "task.review-approved",
            "task.completed",
            "goal.ready-for-review",
        } <= events

    def test_cancel_queued_run(self, db_session, registry):
        goal = create_goal(db_session, "To cancel")
        task = db_session.scalars(select(Task).where(Task.goal_id == goal.id)).first()
        run = Run(task_id=task.id, worker="fake")
        db_session.add(run)
        db_session.commit()
        assert cancel_run(db_session, run.id, registry=registry)
        db_session.commit()
        db_session.refresh(run)
        assert run.status == RunStatus.CANCELLED


class TestQueueReliability:
    def test_stale_run_recovery(self, db_session, registry):
        """A run whose lease expired (dead orchestrator) is failed and its task
        requeued — never executed twice concurrently."""
        from datetime import timedelta

        from nexus.db.models import utcnow
        from nexus.services.queue import claim_next_task, enqueue_ready_tasks, recover_stale_runs

        goal = create_goal(db_session, "Crashy [fake:write=r.md] [fake:review=approved]")
        enqueue_ready_tasks(db_session)
        db_session.commit()
        task = claim_next_task(db_session)
        assert task is not None
        # Simulate an orchestrator that claimed the task, created a run, and died.
        run = Run(
            task_id=task.id,
            worker="fake",
            status=RunStatus.RUNNING,
            orchestrator_id="orch-dead",
            lease_expires_at=utcnow() - timedelta(seconds=5),
            started_at=utcnow() - timedelta(minutes=60),
        )
        db_session.add(run)
        db_session.commit()

        recovered = recover_stale_runs(db_session)
        db_session.commit()
        assert recovered == 1
        db_session.refresh(run)
        db_session.refresh(task)
        assert run.status == RunStatus.FAILED
        assert run.error_category == "infrastructure"
        assert task.status == TaskStatus.QUEUED  # requeued within budget

        # The system then completes normally.
        drain(Orchestrator(registry=registry))
        db_session.expire_all()
        assert db_session.get(Goal, goal.id).status == GoalStatus.REVIEW

    def test_heartbeat_extends_lease(self, db_session, registry):
        """Runs created by the orchestrator carry leases; completed runs keep
        their final heartbeat metadata."""
        create_goal(db_session, "Leased [fake:write=l.md] [fake:review=approved]")
        drain(Orchestrator(registry=registry))
        runs = db_session.scalars(select(Run)).all()
        assert runs
        assert all(
            run.lease_expires_at is not None for run in runs if run.purpose == "implementation"
        )
