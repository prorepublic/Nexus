"""FastAPI application for the Nexus control plane.

Bound to localhost by default; no anonymous remote access is possible because
the server never binds a public interface unless explicitly configured, which
is an owner-approval-gated change (docs/THREAT-MODEL.md).
"""

from collections.abc import Iterator
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import nexus
from nexus.api.schemas import (
    ApprovalDecisionIn,
    ApprovalOut,
    CostModeOut,
    FeedbackReportOut,
    FindingOut,
    GoalCreateIn,
    GoalDetailOut,
    GoalOut,
    HealthOut,
    ListOut,
    OkOut,
    PlanOut,
    PullRequestOut,
    RepositoryCreateIn,
    RepositoryOut,
    RunDetailOut,
    RunEventOut,
    RunOut,
    SettingsOut,
    SystemStatusOut,
    TaskDetailOut,
    TaskOut,
    TrustIn,
    ValidationResultOut,
    WorkerStatusOut,
)
from nexus.config import get_settings
from nexus.db.base import get_session_factory
from nexus.db.models import (
    Approval,
    ExecutionPlan,
    Goal,
    PullRequestRecord,
    Repository,
    ReviewFinding,
    Run,
    Task,
    ValidationResult,
)
from nexus.domain.enums import ApprovalState, GoalStatus, TaskStatus
from nexus.domain.transitions import assert_goal_transition, assert_task_transition
from nexus.policies.cost import DEFAULT_COST_POLICY
from nexus.services.approvals import decide_approval
from nexus.services.events import record_audit
from nexus.services.github_feedback import import_pr_feedback
from nexus.services.orchestrator import cancel_run
from nexus.services.planner import PlanningError, create_plan, select_planner
from nexus.services.repositories import (
    RepositoryError,
    get_repository,
    register_repository,
    set_trust,
)
from nexus.workers.registry import get_registry

app = FastAPI(title="Nexus Control Plane", version=nexus.__version__)

# The dashboard runs on localhost:3400; keep CORS closed to local origins only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3400", "http://127.0.0.1:3400"],
    allow_methods=["*"],
    allow_headers=["Content-Type", "X-Nexus-Client", "X-Nexus-Owner-Token"],
)

# Local-owner protection (ADR-013). Binding to localhost is not sufficient
# against browser-origin attacks. Layers, all enforced here:
# - Host allowlist defeats DNS rebinding (attacker domain resolving to
#   127.0.0.1 arrives with a foreign Host header);
# - an Origin allowlist rejects cross-origin state changes outright;
# - every state-changing request must present the generated local-owner
#   credential (X-Nexus-Owner-Token, constant-time compared). The dashboard
#   never embeds the token in browser JavaScript: its same-origin Next.js
#   proxy reads the chmod-600 token file server-side;
# - the custom X-Nexus-Client header additionally forces a CORS preflight.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
_ALLOWED_ORIGIN_HOSTS = {"localhost", "127.0.0.1"}
_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser clients (CLI, proxy) send no Origin
    try:
        from urllib.parse import urlparse

        return urlparse(origin).hostname in _ALLOWED_ORIGIN_HOSTS
    except ValueError:
        return False


@app.middleware("http")
async def local_owner_protection(request, call_next):  # noqa: ANN001
    from fastapi.responses import JSONResponse

    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host not in _ALLOWED_HOSTS:
        return JSONResponse({"detail": "host not allowed"}, status_code=421)
    if request.method in _STATE_CHANGING:
        if not _origin_allowed(request.headers.get("origin")):
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        if not request.headers.get("x-nexus-client"):
            return JSONResponse(
                {"detail": "missing X-Nexus-Client header (local-owner protection)"},
                status_code=403,
            )
        from nexus.services.owner_auth import verify_token

        if not verify_token(request.headers.get("x-nexus-owner-token")):
            return JSONResponse(
                {
                    "detail": "missing or invalid owner credential "
                    "(X-Nexus-Owner-Token; see ~/.nexus/owner-token)"
                },
                status_code=403,
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def get_db() -> Iterator[Session]:  # pragma: no cover - thin wiring
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _db_status() -> Literal["ok", "unavailable"]:
    try:
        with get_session_factory()() as session:
            session.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "unavailable"


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", version=nexus.__version__, database=_db_status())


@app.get("/api/system/status", response_model=SystemStatusOut)
def system_status() -> SystemStatusOut:
    registry = get_registry()
    workers = []
    provider_names = {
        "claude-code": "Anthropic (Claude Max)",
        "codex-cli": "OpenAI (ChatGPT Plus)",
        "fake": "Nexus built-in",
    }
    for name, health_ in registry.all_health().items():
        workers.append(
            WorkerStatusOut(
                name=str(name),
                provider=provider_names.get(str(name), "unknown"),
                available=health_.available,
                version=health_.version,
                authenticated=health_.authenticated,
                detail=health_.detail,
            )
        )
    cost = DEFAULT_COST_POLICY.describe()
    return SystemStatusOut(
        version=nexus.__version__,
        database=_db_status(),
        cost_mode=CostModeOut(
            paid_apis_enabled=bool(cost["paid_apis_enabled"]), description=str(cost["description"])
        ),
        workers=workers,
    )


def _goal_out(goal: Goal) -> GoalOut:
    return GoalOut(
        id=goal.id,
        title=goal.title,
        description=goal.description,
        status=goal.status,
        priority=goal.priority,
        autonomy=goal.autonomy,
        repository=goal.repository.name if goal.repository else None,
        requested_worker=goal.requested_worker,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )


def _task_out(task: Task, goal_title: str | None = None) -> TaskOut:
    return TaskOut(
        id=task.id,
        goal_id=task.goal_id,
        goal_title=goal_title,
        title=task.title,
        status=task.status,
        kind=task.kind,
        worker=task.worker,
        reviewer=task.reviewer,
        risk=task.risk,
        attempt_count=task.attempt_count,
        branch=task.branch,
        created_at=task.created_at,
    )


@app.post("/api/goals", response_model=GoalOut, status_code=201)
def create_goal(payload: GoalCreateIn, db: Session = Depends(get_db)) -> GoalOut:
    repository = None
    if payload.repository:
        repository = db.scalars(
            select(Repository).where(Repository.name == payload.repository)
        ).first()
        if repository is None:
            raise HTTPException(
                422,
                f"repository '{payload.repository}' is not registered; "
                "register it first (nexus repo add or POST /api/repositories)",
            )
    goal = Goal(
        title=payload.title.strip(),
        description=payload.description.strip(),
        priority=payload.priority,
        autonomy=payload.autonomy,
        requested_worker=payload.requested_worker,
        acceptance_criteria=payload.acceptance_criteria,
        constraints=payload.constraints,
        plan_mode=payload.plan_mode,
        repository_id=repository.id if repository else None,
    )
    db.add(goal)
    db.flush()
    try:
        create_plan(db, goal, select_planner(goal))
    except PlanningError as exc:
        raise HTTPException(502, f"planning failed: {exc}") from None
    record_audit(
        db,
        "goal.created",
        goal_id=goal.id,
        actor="owner",
        metadata={"requested_worker": payload.requested_worker, "plan_mode": payload.plan_mode},
    )
    db.flush()
    return _goal_out(goal)


@app.get("/api/goals", response_model=ListOut[GoalOut])
def list_goals(limit: int = 20, db: Session = Depends(get_db)) -> ListOut[GoalOut]:
    goals = db.scalars(select(Goal).order_by(Goal.created_at.desc()).limit(min(limit, 100))).all()
    return ListOut(items=[_goal_out(goal) for goal in goals])


@app.get("/api/goals/{goal_id}", response_model=GoalDetailOut)
def get_goal(goal_id: str, db: Session = Depends(get_db)) -> GoalDetailOut:
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(404, "goal not found")
    tasks = db.scalars(select(Task).where(Task.goal_id == goal_id).order_by(Task.created_at)).all()
    base = _goal_out(goal)
    return GoalDetailOut(**base.model_dump(), tasks=[_task_out(task) for task in tasks])


@app.post("/api/goals/{goal_id}/cancel", response_model=OkOut)
def cancel_goal(goal_id: str, db: Session = Depends(get_db)) -> OkOut:
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(404, "goal not found")
    try:
        goal.status = assert_goal_transition(GoalStatus(goal.status), GoalStatus.CANCELLED)
    except Exception as exc:
        raise HTTPException(409, f"cannot cancel goal in status {goal.status}") from exc
    for task in db.scalars(select(Task).where(Task.goal_id == goal_id)):
        status = TaskStatus(task.status)
        if status in {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.QUEUED, TaskStatus.BLOCKED}:
            task.status = TaskStatus.CANCELLED
    record_audit(db, "goal.cancel-requested", goal_id=goal_id, actor="owner")
    return OkOut(ok=True)


@app.get("/api/tasks", response_model=ListOut[TaskOut])
def list_tasks(
    status: str | None = None, limit: int = 50, db: Session = Depends(get_db)
) -> ListOut[TaskOut]:
    stmt = select(Task, Goal.title).join(Goal, Task.goal_id == Goal.id)
    if status:
        stmt = stmt.where(Task.status == status)
    stmt = stmt.order_by(Task.created_at.desc()).limit(min(limit, 200))
    rows = db.execute(stmt).all()
    return ListOut(items=[_task_out(task, goal_title) for task, goal_title in rows])


def _run_out(run: Run, task_title: str | None = None) -> RunOut:
    return RunOut(
        id=run.id,
        task_id=run.task_id,
        task_title=task_title,
        worker=run.worker,
        status=run.status,
        attempt=run.attempt,
        started_at=run.started_at,
        finished_at=run.finished_at,
        exit_summary=run.exit_summary,
    )


@app.get("/api/runs", response_model=ListOut[RunOut])
def list_runs(limit: int = 50, db: Session = Depends(get_db)) -> ListOut[RunOut]:
    rows = db.execute(
        select(Run, Task.title)
        .join(Task, Run.task_id == Task.id)
        .order_by(Run.created_at.desc())
        .limit(min(limit, 200))
    ).all()
    return ListOut(items=[_run_out(run, title) for run, title in rows])


@app.get("/api/runs/{run_id}", response_model=RunDetailOut)
def get_run(run_id: str, db: Session = Depends(get_db)) -> RunDetailOut:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    task = db.get(Task, run.task_id)
    base = _run_out(run, task.title if task else None)
    events = [
        RunEventOut(ts=event.ts, type=event.type, status=event.status, message=event.message)
        for event in sorted(run.events, key=lambda item: item.ts)
    ]
    return RunDetailOut(**base.model_dump(), events=events)


@app.post("/api/runs/{run_id}/cancel", response_model=OkOut)
def cancel_run_endpoint(run_id: str, db: Session = Depends(get_db)) -> OkOut:
    return OkOut(ok=cancel_run(db, run_id))


@app.get("/api/approvals", response_model=ListOut[ApprovalOut])
def list_approvals(state: str = "pending", db: Session = Depends(get_db)) -> ListOut[ApprovalOut]:
    approvals = db.scalars(
        select(Approval)
        .where(Approval.state == state)
        .order_by(Approval.created_at.desc())
        .limit(100)
    ).all()
    return ListOut(
        items=[
            ApprovalOut(
                id=a.id,
                kind=a.kind,
                description=a.description,
                risk=a.risk,
                state=a.state,
                requested_at=a.created_at,
            )
            for a in approvals
        ]
    )


@app.post("/api/approvals/{approval_id}/decision", response_model=OkOut)
def decide_approval_endpoint(
    approval_id: str, payload: ApprovalDecisionIn, db: Session = Depends(get_db)
) -> OkOut:
    """Decisions flow through the approvals service so blocked tasks resume
    (or cancel) deterministically."""
    try:
        decide_approval(db, approval_id, payload.decision, payload.note)
    except ValueError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(code, str(exc)) from None
    return OkOut(ok=True)


# --- repositories -------------------------------------------------------------
@app.get("/api/repositories", response_model=ListOut[RepositoryOut])
def list_repositories(db: Session = Depends(get_db)) -> ListOut[RepositoryOut]:
    repos = db.scalars(select(Repository).order_by(Repository.name)).all()
    return ListOut(items=[_repo_out(repo) for repo in repos])


@app.post("/api/repositories", response_model=RepositoryOut, status_code=201)
def register_repository_endpoint(
    payload: RepositoryCreateIn, db: Session = Depends(get_db)
) -> RepositoryOut:
    try:
        repo = register_repository(db, payload.source, name=payload.name)
    except RepositoryError as exc:
        raise HTTPException(422, str(exc)) from None
    record_audit(db, "repository.registered", actor="owner", metadata={"name": repo.name})
    return _repo_out(repo)


@app.get("/api/repositories/{repo_id}", response_model=RepositoryOut)
def get_repository_endpoint(repo_id: str, db: Session = Depends(get_db)) -> RepositoryOut:
    try:
        repo = get_repository(db, repo_id)
    except RepositoryError as exc:
        raise HTTPException(404, str(exc)) from None
    return _repo_out(repo)


@app.post("/api/repositories/{repo_id}/trust", response_model=RepositoryOut)
def set_repository_trust(
    repo_id: str, payload: TrustIn, db: Session = Depends(get_db)
) -> RepositoryOut:
    try:
        repo = get_repository(db, repo_id)
        repo = set_trust(db, repo.name, payload.level)
    except RepositoryError as exc:
        raise HTTPException(422, str(exc)) from None
    record_audit(
        db,
        "repository.trust-changed",
        actor="owner",
        metadata={"name": repo.name, "level": payload.level},
    )
    return _repo_out(repo)


def _repo_out(repo: Repository) -> RepositoryOut:
    return RepositoryOut(
        id=repo.id,
        name=repo.name,
        local_path=repo.local_path,
        github_slug=repo.github_slug,
        default_branch=repo.default_branch,
        trust_level=repo.trust_level,
        onboarded=repo.onboarded,
        languages=[str(x) for x in (repo.languages or [])],
        validation_kinds=sorted((repo.validation_profile or {}).keys()),
    )


# --- plans, task detail, retry --------------------------------------------------
@app.get("/api/goals/{goal_id}/plan", response_model=PlanOut)
def get_plan(goal_id: str, db: Session = Depends(get_db)) -> PlanOut:
    plan = db.scalars(select(ExecutionPlan).where(ExecutionPlan.goal_id == goal_id)).first()
    if plan is None:
        raise HTTPException(404, "no plan for this goal")
    return PlanOut(
        id=plan.id,
        goal_id=goal_id,
        objective=plan.objective,
        assumptions=[str(a) for a in (plan.assumptions or [])],
        risks=[str(r) for r in (plan.risks or [])],
        affected_components=[str(c) for c in (plan.affected_components or [])],
        validation_plan=[str(v) for v in (plan.validation_plan or [])],
        planner=plan.planner,
        proposed_solution=plan.proposed_solution,
    )


@app.post("/api/goals/{goal_id}/approve-plan", response_model=OkOut)
def approve_plan(goal_id: str, db: Session = Depends(get_db)) -> OkOut:
    approval = db.scalars(
        select(Approval)
        .where(Approval.goal_id == goal_id, Approval.kind == "approve-plan")
        .order_by(Approval.created_at.desc())
    ).first()
    if approval is None:
        raise HTTPException(404, "this goal has no pending plan approval")
    if approval.state != ApprovalState.PENDING:
        raise HTTPException(409, f"plan approval already {approval.state}")
    decide_approval(db, approval.id, "approved")
    return OkOut(ok=True)


@app.get("/api/tasks/{task_id}", response_model=TaskDetailOut)
def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskDetailOut:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    goal = db.get(Goal, task.goal_id)
    findings = db.scalars(select(ReviewFinding).where(ReviewFinding.task_id == task_id)).all()
    run_ids = [run.id for run in task.runs]
    validations = (
        db.scalars(select(ValidationResult).where(ValidationResult.run_id.in_(run_ids))).all()
        if run_ids
        else []
    )
    base = _task_out(task, goal.title if goal else None)
    from nexus.observability import redact_text

    return TaskDetailOut(
        **base.model_dump(),
        instruction=redact_text(task.instruction),
        worktree_path=task.worktree_path,
        review_verdict=task.review_verdict,
        validation_results=[
            ValidationResultOut(
                kind=v.kind,
                status=v.status,
                summary=v.summary,
                exit_code=v.exit_code,
                duration_ms=v.duration_ms,
            )
            for v in validations
        ],
        findings=[
            FindingOut(
                id=f.id,
                severity=f.severity,
                category=f.category,
                description=f.description,
                file=f.file,
                line=f.line,
                recommendation=f.recommendation,
                blocking=f.blocking,
                resolved=f.resolved,
                source=f.source,
                reviewer=f.reviewer,
            )
            for f in findings
        ],
    )


@app.post("/api/tasks/{task_id}/retry", response_model=OkOut)
def retry_task(task_id: str, db: Session = Depends(get_db)) -> OkOut:
    """Explicit operator retry of a terminally failed task."""
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if TaskStatus(task.status) != TaskStatus.FAILED:
        raise HTTPException(409, f"only failed tasks can be retried (status: {task.status})")
    task.status = assert_task_transition(TaskStatus.FAILED, TaskStatus.QUEUED)
    task.max_attempts = task.attempt_count + 1  # grant exactly one more attempt
    goal = db.get(Goal, task.goal_id)
    if goal is not None and GoalStatus(goal.status) == GoalStatus.FAILED:
        goal.status = assert_goal_transition(GoalStatus.FAILED, GoalStatus.PLANNING)
        goal.status = assert_goal_transition(GoalStatus.PLANNING, GoalStatus.READY)
    record_audit(db, "task.operator-retry", task_id=task_id, actor="owner")
    return OkOut(ok=True)


# --- pull requests and feedback ---------------------------------------------------
@app.get("/api/pull-requests", response_model=ListOut[PullRequestOut])
def list_pull_requests(db: Session = Depends(get_db)) -> ListOut[PullRequestOut]:
    records = db.scalars(
        select(PullRequestRecord).order_by(PullRequestRecord.created_at.desc()).limit(50)
    ).all()
    return ListOut(
        items=[
            PullRequestOut(
                id=r.id,
                goal_id=r.goal_id,
                repository=r.repository,
                number=r.number,
                url=r.url,
                branch=r.branch,
                state=r.state,
                updated_at=r.updated_at,
            )
            for r in records
        ]
    )


@app.post("/api/goals/{goal_id}/feedback/import", response_model=FeedbackReportOut)
def import_feedback(goal_id: str, db: Session = Depends(get_db)) -> FeedbackReportOut:
    try:
        report = import_pr_feedback(db, goal_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    return FeedbackReportOut(
        fetched=report.fetched,
        actionable=report.actionable,
        ignored=report.ignored,
        duplicates=report.duplicates,
        repair_tasks=report.repair_tasks,
    )


# --- settings -----------------------------------------------------------------
@app.get("/api/settings", response_model=SettingsOut)
def get_settings_endpoint() -> SettingsOut:
    settings = get_settings()
    return SettingsOut(
        review_policy=settings.review_policy,
        planner_mode=settings.planner_mode,
        max_repair_attempts=settings.max_repair_attempts,
        task_timeout_seconds=settings.task_timeout_seconds,
        lease_seconds=settings.lease_seconds,
        cost_mode=CostModeOut(
            paid_apis_enabled=False,
            description=str(DEFAULT_COST_POLICY.describe()["description"]),
        ),
    )
