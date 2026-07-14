"""End-to-end orchestration through the fake worker: the bootstrap's
Definition-of-Done slice, exercised entirely without model usage."""

import pytest
from sqlalchemy import select

from nexus.db.models import AuditEvent, Goal, Run, Task
from nexus.domain.enums import GoalStatus, RunStatus, TaskStatus, WorkerName
from nexus.services.orchestrator import Orchestrator, cancel_run
from nexus.services.planner import DeterministicPlanner, create_plan
from nexus.workers.fake import FakeWorker
from nexus.workers.registry import WorkerRegistry

pytestmark = pytest.mark.integration


@pytest.fixture
def registry():
    return WorkerRegistry({WorkerName.FAKE: FakeWorker()})


def create_goal(session, description, criteria=None, title="Test goal"):
    goal = Goal(
        title=title,
        description=description,
        requested_worker="fake",
        acceptance_criteria=criteria or [],
    )
    session.add(goal)
    session.flush()
    create_plan(session, goal, DeterministicPlanner())
    session.commit()
    return goal


def drain(orchestrator, max_ticks=25):
    for _ in range(max_ticks):
        if not orchestrator.tick():
            break


class TestVerticalSlice:
    def test_goal_to_completion(self, db_session, registry):
        goal = create_goal(db_session, "Do the thing. [fake:write=proof.md]")
        assert goal.status == GoalStatus.READY

        drain(Orchestrator(registry=registry))

        db_session.expire_all()
        goal = db_session.get(Goal, goal.id)
        assert goal.status == GoalStatus.REVIEW  # ready for human review
        tasks = db_session.scalars(select(Task).where(Task.goal_id == goal.id)).all()
        assert tasks and all(t.status == TaskStatus.COMPLETED for t in tasks)
        runs = db_session.scalars(select(Run)).all()
        assert runs and all(r.status == RunStatus.SUCCEEDED for r in runs)

    def test_acceptance_criteria_become_dependent_tasks(self, db_session, registry):
        goal = create_goal(db_session, "Multi-step", criteria=["first", "second"])
        tasks = db_session.scalars(select(Task).where(Task.goal_id == goal.id)).all()
        assert len(tasks) == 3  # two implementation + one review

        drain(Orchestrator(registry=registry))
        db_session.expire_all()
        assert db_session.get(Goal, goal.id).status == GoalStatus.REVIEW

    def test_bounded_repair_recovers_from_transient_failure(self, db_session, registry):
        goal = create_goal(db_session, "Flaky work [fake:fail-first=1]")
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

    def test_audit_trail_exists(self, db_session, registry):
        create_goal(db_session, "Audited [fake:write=a.md]")
        drain(Orchestrator(registry=registry))
        events = {e.event_type for e in db_session.scalars(select(AuditEvent))}
        assert {"run.started", "task.completed", "goal.ready-for-review"} <= events

    def test_cancel_queued_run(self, db_session, registry):
        goal = create_goal(db_session, "To cancel")
        # Manually create a queued run to cancel (orchestrator not started).
        task = db_session.scalars(select(Task).where(Task.goal_id == goal.id)).first()
        run = Run(task_id=task.id, worker="fake")
        db_session.add(run)
        db_session.commit()
        assert cancel_run(db_session, run.id, registry=registry)
        db_session.commit()
        db_session.refresh(run)
        assert run.status == RunStatus.CANCELLED
