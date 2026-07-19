"""The Nexus execution engine (ADR-005, ADR-008..010).

Pipeline per task:

  claim -> gates -> workspace -> instruct -> execute -> validate (fail-closed)
        -> independent review -> commit -> complete
                 |                   |
                 +--> bounded repair with context <--+

Finite autonomy is enforced throughout: bounded attempts, task timeouts,
explicit state transitions, leases with heartbeats, cancellation, approval
gates that block and deterministically resume, and a full audit trail.
Database transactions are never held open while model workers execute.
"""

import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus.config import get_settings
from nexus.db.base import session_scope
from nexus.db.models import Goal, Repository, Run, Task, ValidationResult, utcnow
from nexus.domain.enums import (
    RETRYABLE_FAILURES,
    FailureCategory,
    GoalStatus,
    ReviewVerdict,
    Risk,
    RunStatus,
    TaskKind,
    TaskStatus,
    TrustLevel,
    ValidationKind,
    ValidationStatus,
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
from nexus.services import workspace as ws
from nexus.services.approvals import ApprovalPending, ensure_approval, find_decision
from nexus.services.context import build_task_instruction
from nexus.services.events import record_audit, record_run_event
from nexus.services.queue import claim_next_task, enqueue_ready_tasks, recover_stale_runs
from nexus.services.reviews import execute_review, persist_findings
from nexus.services.validation import evidence_sufficient, validate_workspace
from nexus.workers.base import TaskSpec, WorkerEvent
from nexus.workers.registry import WorkerRegistry, get_registry

log = get_logger(__name__)

_CATEGORY_MAP = {
    "auth": FailureCategory.AUTH,
    "limit": FailureCategory.RATE_LIMIT,
    "timeout": FailureCategory.TIMEOUT,
    "crash": FailureCategory.WORKER_CRASH,
    "cli-flags": FailureCategory.TOOL_UNAVAILABLE,
    "not-installed": FailureCategory.TOOL_UNAVAILABLE,
    "policy": FailureCategory.POLICY_VIOLATION,
    "simulated": FailureCategory.SIMULATED,
    "cancelled": FailureCategory.CANCELLED,
    "invalid-output": FailureCategory.INVALID_OUTPUT,
}

REVIEWED_KINDS = {
    TaskKind.IMPLEMENTATION,
    TaskKind.REFACTORING,
    TaskKind.TESTING,
    TaskKind.MAINTENANCE,
}

WORKTREE_KINDS = {
    TaskKind.IMPLEMENTATION,
    TaskKind.REFACTORING,
    TaskKind.TESTING,
    TaskKind.MAINTENANCE,
    TaskKind.DOCUMENTATION,
}


class Orchestrator:
    def __init__(
        self,
        registry: WorkerRegistry | None = None,
        poll_interval: float = 1.0,
        orchestrator_id: str | None = None,
    ) -> None:
        self.registry = registry or get_registry()
        self.poll_interval = poll_interval
        self.orchestrator_id = orchestrator_id or f"orch-{uuid.uuid4().hex[:10]}"
        self._stop = threading.Event()
        # worker name -> unavailable-until monotonic timestamp (rate limits)
        self._cooldowns: dict[str, float] = {}

    # -- lifecycle ---------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        log.info("orchestrator.started", orchestrator_id=self.orchestrator_id)
        while not self._stop.is_set():
            try:
                worked = self.tick()
            except Exception:
                log.exception("orchestrator.tick_failed")
                worked = False
            if not worked:
                time.sleep(self.poll_interval)
        log.info("orchestrator.stopped", orchestrator_id=self.orchestrator_id)

    def tick(self) -> bool:
        """One scheduling cycle. Returns True if a task was processed."""
        with session_scope() as session:
            recover_stale_runs(session)
            enqueue_ready_tasks(session)
        with session_scope() as session:
            task = claim_next_task(session)
            if task is None:
                self._settle_goals(session)
                return False
            task_id = task.id
        try:
            self.process_task(task_id)
        finally:
            with session_scope() as session:
                self._settle_goals(session)
        return True

    # -- worker selection ------------------------------------------------------
    def _available_workers(self) -> set[WorkerName]:
        now = time.monotonic()
        return {
            name
            for name in self.registry.available_names()
            if self._cooldowns.get(str(name), 0) < now
        }

    def _select_worker(self, session: Session, task: Task, goal: Goal) -> str:
        requested = WorkerName(goal.requested_worker) if goal.requested_worker else None
        if task.worker and self._cooldowns.get(task.worker, 0) < time.monotonic():
            return task.worker  # sticky routing across repair attempts
        router = Router(available=self._available_workers())
        decision = router.route(TaskKind(task.kind), requested_worker=requested)
        task.reviewer = decision.reviewer
        record_audit(
            session,
            "task.routed",
            task_id=task.id,
            goal_id=goal.id,
            metadata={
                "worker": str(decision.worker),
                "reviewer": str(decision.reviewer) if decision.reviewer else None,
                "reason": decision.reason,
            },
        )
        return str(decision.worker)

    # -- workspaces --------------------------------------------------------------
    def _resolve_workspace(
        self, session: Session, task: Task, goal: Goal, repo: Repository | None
    ) -> Path:
        """Isolated worktree for repository-backed tasks; scratch otherwise.

        Sequentially dependent tasks chain: each task's worktree starts from
        the branch head of its most recent completed dependency, so the final
        task's branch carries the whole goal and becomes the PR head.
        """
        if task.worktree_path and Path(task.worktree_path).exists():
            return Path(task.worktree_path)

        if repo is not None and repo.local_path and TaskKind(task.kind) in WORKTREE_KINDS:
            if not repo.onboarded:
                raise ws.WorkspaceError(
                    f"repository {repo.name} has not passed onboarding (run `nexus repo doctor`)"
                )
            start_point = self._baseline_for(session, task)
            workspace = ws.create_workspace(
                Path(repo.local_path),
                task.goal_id,
                task.id,
                title=task.title,
                start_point=start_point,
            )
            task.branch = workspace.branch
            task.worktree_path = str(workspace.worktree_path)
            task.baseline_commit = workspace.baseline_commit
            record_audit(
                session,
                "task.worktree-created",
                task_id=task.id,
                goal_id=task.goal_id,
                metadata={
                    "branch": workspace.branch,
                    "baseline": workspace.baseline_commit[:12],
                    "start_point": start_point,
                },
            )
            return workspace.worktree_path

        # Review tasks in a repo goal inspect the final implementation worktree.
        if repo is not None and TaskKind(task.kind) in {TaskKind.REVIEW, TaskKind.SECURITY_REVIEW}:
            last = self._latest_completed_impl(session, task.goal_id)
            if last is not None and last.worktree_path:
                return Path(last.worktree_path)

        scratch = get_settings().workspaces_dir / "scratch" / task.goal_id
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    def _latest_completed_impl(self, session: Session, goal_id: str) -> Task | None:
        return session.scalars(
            select(Task)
            .where(
                Task.goal_id == goal_id,
                Task.status == TaskStatus.COMPLETED,
                Task.worktree_path.is_not(None),
            )
            .order_by(Task.completed_at.desc())
        ).first()

    def _baseline_for(self, session: Session, task: Task) -> str | None:
        """Branch of the most recent completed dependency (chained worktrees)."""
        from nexus.db.models import TaskDependency

        dep_ids = session.scalars(
            select(TaskDependency.depends_on_task_id).where(TaskDependency.task_id == task.id)
        ).all()
        if not dep_ids:
            return None
        deps = session.scalars(
            select(Task)
            .where(Task.id.in_(dep_ids), Task.status == TaskStatus.COMPLETED)
            .order_by(Task.completed_at.desc())
        ).all()
        for dep in deps:
            if dep.branch:
                return dep.branch
        return None

    # -- task processing ----------------------------------------------------
    def process_task(self, task_id: str) -> None:
        settings = get_settings()

        # Phase 1: preparation inside a short transaction.
        with session_scope() as session:
            task = session.get(Task, task_id)
            assert task is not None
            goal = session.get(Goal, task.goal_id)
            assert goal is not None
            repo = goal.repository
            if GoalStatus(goal.status) == GoalStatus.READY:
                goal.status = assert_goal_transition(GoalStatus.READY, GoalStatus.EXECUTING)

            resume_stage = (task.context or {}).get("resume_stage")
            if resume_stage:
                # Approval-gated resumption is not a new worker attempt.
                task.attempt_count = max(1, task.attempt_count - 1)

            try:
                worker_name = self._select_worker(session, task, goal)
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

            try:
                workspace = self._resolve_workspace(session, task, goal, repo)
            except ws.WorkspaceError as exc:
                task.status = assert_task_transition(TaskStatus.RUNNING, TaskStatus.FAILED)
                record_audit(
                    session,
                    "task.workspace-failed",
                    task_id=task.id,
                    goal_id=goal.id,
                    status="failed",
                    metadata={"error": str(exc)[:500]},
                )
                return

            run = Run(
                task_id=task.id,
                worker=worker_name,
                attempt=task.attempt_count,
                purpose="implementation",
                orchestrator_id=self.orchestrator_id,
                lease_expires_at=utcnow() + timedelta(seconds=settings.lease_seconds),
            )
            session.add(run)
            session.flush()
            run.status = assert_run_transition(RunStatus.QUEUED, RunStatus.RUNNING)
            run.started_at = utcnow()
            run_id = run.id
            record_audit(
                session,
                "run.started",
                goal_id=goal.id,
                task_id=task.id,
                run_id=run_id,
                metadata={
                    "worker": worker_name,
                    "attempt": task.attempt_count,
                    "resume_stage": resume_stage,
                },
            )
            instruction = (
                task.instruction
                if resume_stage
                else build_task_instruction(session, task, goal, repo)
            )
            spec = TaskSpec(
                task_id=task.id,
                run_id=run_id,
                kind=TaskKind(task.kind),
                instruction=instruction,
                workspace=workspace,
                timeout_seconds=settings.task_timeout_seconds,
                read_only=TaskKind(task.kind)
                in {TaskKind.REVIEW, TaskKind.SECURITY_REVIEW, TaskKind.PLANNING},
            )

        if resume_stage in {"validation", "finalize"}:
            self._finalize_task(task_id, run_id, spec, skip_review=resume_stage == "finalize")
            return

        # Phase 2: worker execution outside any transaction, with heartbeat.
        adapter = self.registry.get(worker_name)
        adapter.prepare_workspace(spec)
        result = self._execute_with_heartbeat(adapter, spec, run_id)

        # Phase 3: record the worker outcome.
        with session_scope() as session:
            task = session.get(Task, task_id)
            run3 = session.get(Run, run_id)
            assert task is not None and run3 is not None
            run = run3
            run.session_ref = result.session_ref
            run.exit_code = result.exit_code
            run.exit_summary = result.summary[:4000]
            run.error_category = result.error_category
            run.usage = result.usage or {}
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
                category = _CATEGORY_MAP.get(result.error_category or "", FailureCategory.UNKNOWN)
                self._handle_failure(session, task, run, category, detail=result.summary[:500])
                return

        self._finalize_task(task_id, run_id, spec, skip_review=False)

    def _execute_with_heartbeat(self, adapter, spec: TaskSpec, run_id: str):
        settings = get_settings()
        stop_heartbeat = threading.Event()

        def heartbeat() -> None:
            while not stop_heartbeat.wait(settings.heartbeat_seconds):
                try:
                    with session_scope() as session:
                        run = session.get(Run, run_id)
                        if run is None or run.status != RunStatus.RUNNING:
                            return
                        run.heartbeat_at = utcnow()
                        run.lease_expires_at = utcnow() + timedelta(seconds=settings.lease_seconds)
                except Exception:
                    log.exception("orchestrator.heartbeat_failed")

        thread = threading.Thread(target=heartbeat, daemon=True, name=f"hb-{run_id}")
        thread.start()

        def on_event(event: WorkerEvent) -> None:
            with session_scope() as inner:
                record_run_event(inner, run_id, event.type, event.message, metadata=event.metadata)

        try:
            return adapter.execute(spec, on_event=on_event)
        finally:
            stop_heartbeat.set()
            thread.join(timeout=5)

    # -- validation / review / completion -------------------------------------
    def _finalize_task(
        self, task_id: str, run_id: str, spec: TaskSpec, *, skip_review: bool
    ) -> None:
        settings = get_settings()
        with session_scope() as session:
            task = session.get(Task, task_id)
            run = session.get(Run, run_id)
            assert task is not None and run is not None
            goal = session.get(Goal, task.goal_id)
            assert goal is not None
            repo = goal.repository
            if RunStatus(run.status) == RunStatus.RUNNING:
                run.status = assert_run_transition(RunStatus.RUNNING, RunStatus.SUCCEEDED)
                run.finished_at = utcnow()

            task.status = assert_task_transition(TaskStatus(task.status), TaskStatus.VALIDATING)
            context = dict(task.context or {})
            context.pop("resume_stage", None)
            task.context = context

            trust = TrustLevel(repo.trust_level) if repo is not None else TrustLevel.TRUSTED_LOCAL
            approval = find_decision(session, "run-untrusted-repository-scripts", task_id=task.id)
            if approval is not None and approval.state == "approved":
                trust = TrustLevel.TRUSTED_OWNER_APPROVED

            changed: list[str] = []
            workspace_obj = None
            if task.worktree_path and repo is not None and repo.local_path:
                workspace_obj = ws.Workspace(
                    repo_path=Path(repo.local_path),
                    worktree_path=Path(task.worktree_path),
                    branch=task.branch or "",
                    baseline_commit=task.baseline_commit or "",
                )
                changed = ws.changed_files(workspace_obj)

            kinds = [ValidationKind(v) for v in (task.validation_spec or [])]
            if not kinds and task.expected_files:
                kinds = [ValidationKind.FILES_EXIST]
            outcomes = validate_workspace(
                spec.workspace,
                kinds,
                profile_commands={
                    key: value["command"] for key, value in (repo.validation_profile or {}).items()
                }
                if repo is not None
                else {},
                trust_level=trust,
                expected_files=list(task.expected_files or []),
                changed=changed,
                allowed_globs=list(task.scope_globs or []),
                run_id=run_id,
            )
            for outcome in outcomes:
                session.add(
                    ValidationResult(
                        run_id=run_id,
                        kind=str(outcome.kind),
                        status=str(outcome.status),
                        passed=outcome.passed,
                        summary=outcome.summary[:2000],
                        command=outcome.command,
                        profile=outcome.profile,
                        exit_code=outcome.exit_code,
                        duration_ms=outcome.duration_ms,
                        output_tail=outcome.output_tail[-2000:],
                    )
                )
                record_run_event(
                    session,
                    run_id,
                    "validation",
                    outcome.summary,
                    status=str(outcome.status),
                    metadata={"kind": str(outcome.kind)},
                )

            blocked_by_trust = [
                outcome
                for outcome in outcomes
                if outcome.status == ValidationStatus.BLOCKED and "trust level" in outcome.summary
            ]
            if blocked_by_trust:
                try:
                    ensure_approval(
                        session,
                        "run-untrusted-repository-scripts",
                        f"Task '{task.title}' needs to run repository-defined "
                        "validation commands on the host: "
                        + "; ".join(" ".join(o.command) for o in blocked_by_trust[:3])
                        + f". Repository trust level is '{trust}'.",
                        risk=Risk.HIGH,
                        goal_id=goal.id,
                        task_id=task.id,
                    )
                except ApprovalPending:
                    task.status = assert_task_transition(TaskStatus.VALIDATING, TaskStatus.BLOCKED)
                    task.context = {**(task.context or {}), "resume_stage": "validation"}
                    record_audit(
                        session,
                        "task.blocked-on-approval",
                        task_id=task.id,
                        goal_id=goal.id,
                        metadata={"kind": "run-untrusted-repository-scripts"},
                    )
                    return

            sufficient, reason = evidence_sufficient(
                outcomes, TaskKind(task.kind), repository_backed=workspace_obj is not None
            )
            if not sufficient:
                failed_summaries = [f"{o.kind}: {o.summary}" for o in outcomes if not o.passed] or [
                    reason
                ]
                self._record_repair_context(task, failed_validations=failed_summaries)
                # Configuration-blocked checks (no command configured) are
                # deterministic: retrying would burn model usage without any
                # chance of success, so they fail fast for operator correction.
                real_failures = [
                    o
                    for o in outcomes
                    if o.status in {ValidationStatus.FAILED, ValidationStatus.ERROR}
                ]
                config_blocked = [
                    o
                    for o in outcomes
                    if o.status == ValidationStatus.BLOCKED and "trust level" not in o.summary
                ]
                category = (
                    FailureCategory.TOOL_UNAVAILABLE
                    if config_blocked and not real_failures
                    else FailureCategory.VALIDATION_FAILED
                )
                self._handle_failure(session, task, run, category, detail=reason)
                return

            needs_review = (
                not skip_review
                and settings.review_policy != "disabled"
                and TaskKind(task.kind) in REVIEWED_KINDS
            )
            task.status = assert_task_transition(TaskStatus.VALIDATING, TaskStatus.REVIEW)
            if needs_review:
                validation_summaries = [f"{o.kind}: {o.status} - {o.summary}" for o in outcomes]
                may_proceed = self._run_review(
                    session, task, goal, run, workspace_obj, spec, changed, validation_summaries
                )
                if not may_proceed:
                    return
            else:
                record_audit(
                    session,
                    "task.review-skipped",
                    task_id=task.id,
                    metadata={
                        "policy": settings.review_policy,
                        "kind": str(task.kind),
                        "resume_finalize": skip_review,
                    },
                )

            # Commit validated, reviewed work on the task branch.
            if workspace_obj is not None and ws.has_changes(workspace_obj):
                commit = ws.commit_all(
                    workspace_obj,
                    f"nexus: {task.title[:80]}\n\nGoal: {goal.title[:100]}\n"
                    f"Task: {task.id}\nRun: {run_id}",
                )
                if commit:
                    record_audit(
                        session,
                        "task.committed",
                        task_id=task.id,
                        metadata={"commit": commit[:12], "branch": task.branch},
                    )

            task.status = assert_task_transition(TaskStatus(task.status), TaskStatus.COMPLETED)
            task.completed_at = utcnow()
            record_audit(
                session,
                "task.completed",
                goal_id=task.goal_id,
                task_id=task.id,
                run_id=run_id,
                status="completed",
            )

    def _run_review(
        self,
        session: Session,
        task: Task,
        goal: Goal,
        impl_run: Run,
        workspace_obj,
        spec: TaskSpec,
        changed: list[str],
        validation_summaries: list[str],
    ) -> bool:
        """Execute the independent review. Returns True when the task may
        proceed to completion; False when repair/block/fail was scheduled."""
        settings = get_settings()
        available = {str(name) for name in self._available_workers()}
        reviewer = task.reviewer
        if reviewer is None or reviewer not in available or reviewer == task.worker:
            candidates = sorted(available - {task.worker or "", str(WorkerName.FAKE)})
            if not candidates and task.worker == str(WorkerName.FAKE):
                # The fake worker may review its own kind in tests; it is never
                # a meaningful reviewer for real workers.
                candidates = [str(WorkerName.FAKE)] if str(WorkerName.FAKE) in available else []
            reviewer = candidates[0] if candidates else None
            task.reviewer = reviewer

        if reviewer is None:
            if settings.review_policy == "required":
                try:
                    ensure_approval(
                        session,
                        "single-worker-review-fallback",
                        f"Task '{task.title}' passed validation but no independent "
                        "reviewer is available. Approve to complete without "
                        "cross-agent review.",
                        risk=Risk.MEDIUM,
                        goal_id=goal.id,
                        task_id=task.id,
                    )
                    return True  # a prior approval exists
                except ApprovalPending:
                    task.status = assert_task_transition(TaskStatus.REVIEW, TaskStatus.BLOCKED)
                    task.context = {**(task.context or {}), "resume_stage": "finalize"}
                    return False
            record_audit(
                session,
                "task.review-single-worker-fallback",
                task_id=task.id,
                metadata={"policy": settings.review_policy},
            )
            return True

        diff = ws.capture_diff(workspace_obj) if workspace_obj is not None else ""
        adapter = self.registry.get(WorkerName(reviewer))

        review_result = None
        review_run_id: str | None = None
        for review_attempt in range(1, settings.max_review_attempts + 1):
            review_run = Run(
                task_id=task.id,
                worker=reviewer,
                attempt=review_attempt,
                purpose="review",
                orchestrator_id=self.orchestrator_id,
                lease_expires_at=utcnow() + timedelta(seconds=settings.lease_seconds),
            )
            session.add(review_run)
            session.flush()
            review_run.status = assert_run_transition(RunStatus.QUEUED, RunStatus.RUNNING)
            review_run.started_at = utcnow()
            review_run_id = review_run.id

            review_result = execute_review(
                adapter,
                run_id=review_run_id,
                task=task,
                goal=goal,
                workspace=spec.workspace,
                diff=diff,
                changed=changed,
                validation_summaries=validation_summaries,
                timeout_seconds=settings.task_timeout_seconds,
            )
            review_run.finished_at = utcnow()
            review_run.exit_summary = review_result.summary[:2000]
            review_run.status = assert_run_transition(
                RunStatus.RUNNING,
                RunStatus.SUCCEEDED if review_result.raw_ok else RunStatus.FAILED,
            )
            record_run_event(
                session,
                review_run_id,
                "review",
                f"verdict: {review_result.verdict}",
                status=str(review_result.verdict),
            )
            if review_result.raw_ok:
                break
            log.warning("review.invalid_output", attempt=review_attempt, task_id=task.id)

        assert review_result is not None
        persist_findings(session, review_result, task, review_run_id, reviewer)
        task.review_verdict = str(review_result.verdict)

        if review_result.verdict == ReviewVerdict.APPROVED:
            record_audit(
                session,
                "task.review-approved",
                task_id=task.id,
                run_id=review_run_id,
                metadata={"reviewer": reviewer},
            )
            return True

        if review_result.verdict == ReviewVerdict.CHANGES_REQUESTED:
            findings_text = [
                f"[{f['severity']}] {f['description']}"
                + (f" (file: {f['file']})" if f.get("file") else "")
                + (f" Recommendation: {f['recommendation']}" if f.get("recommendation") else "")
                for f in (review_result.blocking_findings or review_result.findings)
            ]
            self._record_repair_context(task, review_findings=findings_text)
            record_audit(
                session,
                "task.review-changes-requested",
                task_id=task.id,
                run_id=review_run_id,
                metadata={"reviewer": reviewer, "blocking": len(review_result.blocking_findings)},
            )
            self._handle_failure(
                session,
                task,
                impl_run,
                FailureCategory.REVIEW_REJECTED,
                detail=review_result.summary[:300],
            )
            return False

        # BLOCKED or unparseable after bounded review attempts.
        if settings.review_policy == "required":
            try:
                ensure_approval(
                    session,
                    "single-worker-review-fallback",
                    f"Independent review of task '{task.title}' could not produce a "
                    f"verdict ({review_result.parse_error or 'blocked'}). Approve to "
                    "complete without a review verdict.",
                    risk=Risk.MEDIUM,
                    goal_id=goal.id,
                    task_id=task.id,
                )
                return True
            except ApprovalPending:
                task.status = assert_task_transition(TaskStatus.REVIEW, TaskStatus.BLOCKED)
                task.context = {**(task.context or {}), "resume_stage": "finalize"}
                return False
        record_audit(
            session,
            "task.review-inconclusive-proceeding",
            task_id=task.id,
            metadata={"policy": settings.review_policy, "error": review_result.parse_error},
        )
        return True

    # -- failure handling -----------------------------------------------------
    def _record_repair_context(
        self,
        task: Task,
        failed_validations: list[str] | None = None,
        review_findings: list[str] | None = None,
        pr_feedback: list[str] | None = None,
    ) -> None:
        context: dict[str, Any] = dict(task.context or {})
        if failed_validations is not None:
            context["failed_validations"] = failed_validations[:20]
        if review_findings is not None:
            context["review_findings"] = review_findings[:20]
        if pr_feedback is not None:
            context["pr_feedback"] = pr_feedback[:20]
        history = list(context.get("attempt_history") or [])
        history.append(
            f"attempt {task.attempt_count}: "
            + (
                "validation failed"
                if failed_validations
                else "review requested changes"
                if review_findings
                else "pr feedback"
            )
        )
        context["attempt_history"] = history[-10:]
        task.context = context

    def _handle_failure(
        self,
        session: Session,
        task: Task,
        run: Run,
        category: FailureCategory,
        detail: str = "",
    ) -> None:
        settings = get_settings()
        current = TaskStatus(task.status)

        if category == FailureCategory.RATE_LIMIT:
            # Subscription/rate limits: cool the worker down, reroute, and do
            # not consume the repair budget.
            if task.worker:
                self._cooldowns[task.worker] = time.monotonic() + settings.worker_cooldown_seconds
                record_audit(
                    session,
                    "worker.rate-limited",
                    task_id=task.id,
                    metadata={
                        "worker": task.worker,
                        "cooldown_s": settings.worker_cooldown_seconds,
                    },
                )
            task.worker = None
            task.attempt_count = max(0, task.attempt_count - 1)
            task.status = assert_task_transition(current, TaskStatus.FAILED)
            task.status = assert_task_transition(TaskStatus.FAILED, TaskStatus.QUEUED)
            return

        retryable = category in RETRYABLE_FAILURES
        repair_budget = min(task.max_attempts, 1 + settings.max_repair_attempts)
        if retryable and task.attempt_count < repair_budget:
            if current in {TaskStatus.VALIDATING, TaskStatus.REVIEW}:
                task.status = assert_task_transition(current, TaskStatus.REPAIRING)
                task.status = assert_task_transition(TaskStatus.REPAIRING, TaskStatus.QUEUED)
            else:  # RUNNING
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
                    "category": str(category),
                    "detail": detail[:300],
                },
            )
        else:
            if current in {TaskStatus.VALIDATING, TaskStatus.RUNNING, TaskStatus.REVIEW}:
                task.status = assert_task_transition(current, TaskStatus.FAILED)
            record_audit(
                session,
                "task.failed",
                goal_id=task.goal_id,
                task_id=task.id,
                run_id=run.id,
                status="failed",
                metadata={
                    "attempts": task.attempt_count,
                    "budget": repair_budget,
                    "category": str(category),
                    "retryable": retryable,
                    "detail": detail[:300],
                },
            )

    # -- goal settlement -----------------------------------------------------
    def _settle_goals(self, session: Session) -> None:
        """Move executing goals to review/failed when all tasks are terminal,
        then deliver (push + draft PR) repository-backed goals."""
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
                self._deliver_goal(session, goal)
            elif TaskStatus.CANCELLED in statuses and TaskStatus.FAILED not in statuses:
                goal.status = assert_goal_transition(GoalStatus.EXECUTING, GoalStatus.CANCELLED)
                record_audit(session, "goal.cancelled", goal_id=goal.id)
            else:
                goal.status = assert_goal_transition(GoalStatus.EXECUTING, GoalStatus.FAILED)
                record_audit(session, "goal.failed", goal_id=goal.id)

    def _deliver_goal(self, session: Session, goal: Goal) -> None:
        from nexus.services.delivery import deliver_goal

        try:
            deliver_goal(session, goal)
        except Exception as exc:
            record_audit(
                session, "goal.delivery-failed", goal_id=goal.id, metadata={"error": str(exc)[:500]}
            )
            log.exception("goal.delivery_failed", goal_id=goal.id)


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
