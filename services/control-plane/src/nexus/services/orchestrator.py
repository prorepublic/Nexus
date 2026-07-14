"""The Nexus execution loop.

Goal -> Plan -> Tasks -> Dispatch -> Execute -> Validate -> Repair -> Complete

Finite autonomy is enforced here: bounded attempts, task timeouts, explicit
state transitions, cancellation support, and a full audit trail. The loop is
synchronous and single-threaded per orchestrator instance; concurrency comes
from running multiple instances against the SKIP LOCKED queue.
"""

import threading
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus.config import get_settings
from nexus.db.base import session_scope
from nexus.db.models import Goal, Run, Task, ValidationResult, utcnow
from nexus.domain.enums import (
    GoalStatus,
    RunStatus,
    TaskKind,
    TaskStatus,
    ValidationKind,
    WorkerName,
)
from nexus.domain.transitions import (
    TERMINAL_TASK_STATUSES,
    assert_goal_transition,
    assert_run_transition,
    assert_task_transition,
)
from nexus.observability import get_logger
from nexus.routing.router import NoWorkerAvailable, Router
from nexus.services.events import record_audit, record_run_event
from nexus.services.queue import claim_next_task, enqueue_ready_tasks
from nexus.services.validation import validate_workspace
from nexus.workers.base import TaskSpec, WorkerEvent
from nexus.workers.registry import WorkerRegistry, get_registry

log = get_logger(__name__)


class Orchestrator:
    def __init__(self, registry: WorkerRegistry | None = None, poll_interval: float = 1.0) -> None:
        self.registry = registry or get_registry()
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._active_runs: dict[str, WorkerName] = {}

    # -- lifecycle ---------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        log.info("orchestrator.started")
        while not self._stop.is_set():
            worked = self.tick()
            if not worked:
                time.sleep(self.poll_interval)
        log.info("orchestrator.stopped")

    def tick(self) -> bool:
        """One scheduling cycle. Returns True if a task was processed."""
        with session_scope() as session:
            enqueue_ready_tasks(session)
        with session_scope() as session:
            task = claim_next_task(session)
            if task is None:
                self._settle_goals(session)
                return False
            task_id = task.id
        self.process_task(task_id)
        with session_scope() as session:
            self._settle_goals(session)
        return True

    # -- task processing ----------------------------------------------------
    def process_task(self, task_id: str) -> None:
        settings = get_settings()
        with session_scope() as session:
            task = session.get(Task, task_id)
            assert task is not None
            goal = session.get(Goal, task.goal_id)
            assert goal is not None
            if GoalStatus(goal.status) == GoalStatus.READY:
                goal.status = assert_goal_transition(GoalStatus.READY, GoalStatus.EXECUTING)

            try:
                worker_name = self._select_worker(task)
            except NoWorkerAvailable as exc:
                task.status = assert_task_transition(TaskStatus.RUNNING, TaskStatus.FAILED)
                record_audit(
                    session,
                    "task.no-worker",
                    task_id=task.id,
                    goal_id=goal.id,
                    status="failed",
                    metadata={"error": str(exc)},
                )
                return
            task.worker = worker_name

            new_run = Run(task_id=task.id, worker=worker_name, attempt=task.attempt_count)
            session.add(new_run)
            session.flush()
            new_run.status = assert_run_transition(RunStatus.QUEUED, RunStatus.RUNNING)
            new_run.started_at = utcnow()
            run_id = new_run.id
            record_audit(
                session,
                "run.started",
                goal_id=goal.id,
                task_id=task.id,
                run_id=run_id,
                metadata={"worker": worker_name, "attempt": task.attempt_count},
            )
            spec = TaskSpec(
                task_id=task.id,
                run_id=run_id,
                kind=TaskKind(task.kind),
                instruction=task.instruction,
                workspace=self._workspace_for(task),
                timeout_seconds=settings.task_timeout_seconds,
                read_only=TaskKind(task.kind)
                in {TaskKind.REVIEW, TaskKind.SECURITY_REVIEW, TaskKind.PLANNING},
            )

        adapter = self.registry.get(worker_name)
        adapter.prepare_workspace(spec)
        self._active_runs[run_id] = WorkerName(worker_name)

        def on_event(event: WorkerEvent) -> None:
            with session_scope() as inner:
                record_run_event(inner, run_id, event.type, event.message, metadata=event.metadata)

        try:
            result = adapter.execute(spec, on_event=on_event)
        finally:
            self._active_runs.pop(run_id, None)

        with session_scope() as session:
            task = session.get(Task, task_id)
            run = session.get(Run, run_id)
            assert task is not None and run is not None
            run.session_ref = result.session_ref
            run.exit_code = result.exit_code
            run.exit_summary = result.summary[:4000]
            run.error_category = result.error_category
            run.finished_at = utcnow()

            if run.cancel_requested or result.error_category == "cancelled":
                run.status = assert_run_transition(RunStatus(run.status), RunStatus.CANCELLED)
                task.status = assert_task_transition(TaskStatus(task.status), TaskStatus.CANCELLED)
                record_audit(session, "run.cancelled", task_id=task.id, run_id=run_id)
                return

            if result.error_category == "timeout":
                run.status = assert_run_transition(RunStatus(run.status), RunStatus.TIMED_OUT)
            elif result.ok:
                run.status = assert_run_transition(RunStatus(run.status), RunStatus.SUCCEEDED)
            else:
                run.status = assert_run_transition(RunStatus(run.status), RunStatus.FAILED)

            if not result.ok:
                self._handle_failure(session, task, run)
                return

            # Worker claims success — verify with evidence.
            task.status = assert_task_transition(TaskStatus(task.status), TaskStatus.VALIDATING)
            outcomes = validate_workspace(
                Path(self._workspace_for(task)),
                [ValidationKind(v) for v in (task.validation_spec or ["files-exist"])],
                expected_files=list(task.expected_files or []),
            )
            all_passed = all(outcome.passed for outcome in outcomes)
            for outcome in outcomes:
                session.add(
                    ValidationResult(
                        run_id=run_id,
                        kind=outcome.kind,
                        passed=outcome.passed,
                        summary=outcome.summary[:2000],
                    )
                )
                record_run_event(
                    session,
                    run_id,
                    "validation",
                    outcome.summary,
                    status="passed" if outcome.passed else "failed",
                    metadata={"kind": str(outcome.kind)},
                )

            if all_passed:
                task.status = assert_task_transition(TaskStatus.VALIDATING, TaskStatus.COMPLETED)
                task.completed_at = utcnow()
                record_audit(
                    session,
                    "task.completed",
                    goal_id=task.goal_id,
                    task_id=task.id,
                    run_id=run_id,
                    status="completed",
                )
            else:
                self._handle_failure(session, task, run, from_validation=True)

    def _handle_failure(
        self, session: Session, task: Task, run: Run, from_validation: bool = False
    ) -> None:
        settings = get_settings()
        repair_budget = min(task.max_attempts, 1 + settings.max_repair_attempts)
        current = TaskStatus(task.status)
        if task.attempt_count < repair_budget:
            if current == TaskStatus.VALIDATING:
                task.status = assert_task_transition(current, TaskStatus.REPAIRING)
                task.status = assert_task_transition(TaskStatus.REPAIRING, TaskStatus.QUEUED)
            else:  # RUNNING (worker failure before validation)
                task.status = assert_task_transition(current, TaskStatus.FAILED)
                task.status = assert_task_transition(TaskStatus.FAILED, TaskStatus.QUEUED)
            record_audit(
                session,
                "task.repair-scheduled",
                task_id=task.id,
                run_id=run.id,
                metadata={
                    "attempt": task.attempt_count,
                    "budget": repair_budget,
                    "from_validation": from_validation,
                },
            )
        else:
            if current == TaskStatus.VALIDATING:
                task.status = assert_task_transition(current, TaskStatus.FAILED)
            elif current == TaskStatus.RUNNING:
                task.status = assert_task_transition(current, TaskStatus.FAILED)
            record_audit(
                session,
                "task.failed",
                goal_id=task.goal_id,
                task_id=task.id,
                run_id=run.id,
                status="failed",
                metadata={"attempts": task.attempt_count, "budget": repair_budget},
            )

    # -- goal settlement -----------------------------------------------------
    def _settle_goals(self, session: Session) -> None:
        """Move executing goals to review/failed when all tasks are terminal."""
        goals = session.scalars(select(Goal).where(Goal.status == GoalStatus.EXECUTING)).all()
        for goal in goals:
            tasks = session.scalars(select(Task).where(Task.goal_id == goal.id)).all()
            if not tasks:
                continue
            statuses = {TaskStatus(task.status) for task in tasks}
            if not statuses <= TERMINAL_TASK_STATUSES:
                continue
            if statuses == {TaskStatus.COMPLETED}:
                goal.status = assert_goal_transition(GoalStatus.EXECUTING, GoalStatus.REVIEW)
                record_audit(session, "goal.ready-for-review", goal_id=goal.id)
            elif TaskStatus.CANCELLED in statuses and TaskStatus.FAILED not in statuses:
                goal.status = assert_goal_transition(GoalStatus.EXECUTING, GoalStatus.CANCELLED)
                record_audit(session, "goal.cancelled", goal_id=goal.id)
            else:
                goal.status = assert_goal_transition(GoalStatus.EXECUTING, GoalStatus.FAILED)
                record_audit(session, "goal.failed", goal_id=goal.id)

    # -- helpers -------------------------------------------------------------
    def _select_worker(self, task: Task) -> str:
        goal = task.goal
        requested = None
        if goal is not None and goal.requested_worker:
            requested = WorkerName(goal.requested_worker)
        if task.worker:  # already routed on a previous attempt
            return task.worker
        router = Router(available=self.registry.available_names())
        decision = router.route(TaskKind(task.kind), requested_worker=requested)
        task.reviewer = decision.reviewer
        return decision.worker

    def _workspace_for(self, task: Task) -> Path:
        """Workspace resolution.

        If the task already has an isolated worktree recorded, use it. Otherwise
        fall back to a scratch directory per goal (used by the fake-worker slice;
        real repository tasks get worktrees via nexus.services.workspace).
        """
        if task.worktree_path:
            return Path(task.worktree_path)
        scratch = get_settings().workspaces_dir / "scratch" / task.goal_id
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch


def cancel_run(session: Session, run_id: str, registry: WorkerRegistry | None = None) -> bool:
    """Request cancellation of a queued or running run."""
    registry = registry or get_registry()
    run = session.get(Run, run_id)
    if run is None:
        return False
    status = RunStatus(run.status)
    if status == RunStatus.QUEUED:
        run.status = assert_run_transition(status, RunStatus.CANCELLED)
        run.cancel_requested = True
        return True
    if status == RunStatus.RUNNING:
        run.cancel_requested = True
        adapter = registry.get(WorkerName(run.worker))
        adapter.cancel(run_id)
        record_audit(session, "run.cancel-requested", run_id=run_id)
        return True
    return False
