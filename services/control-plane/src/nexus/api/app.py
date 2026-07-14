"""FastAPI application for the Nexus control plane.

Bound to localhost by default; no anonymous remote access is possible because
the server never binds a public interface unless explicitly configured, which
is an owner-approval-gated change (docs/THREAT-MODEL.md).
"""

from collections.abc import Iterator
from datetime import UTC, datetime
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
    GoalCreateIn,
    GoalDetailOut,
    GoalOut,
    HealthOut,
    ListOut,
    OkOut,
    RunDetailOut,
    RunEventOut,
    RunOut,
    SystemStatusOut,
    TaskOut,
    WorkerStatusOut,
)
from nexus.db.base import get_session_factory
from nexus.db.models import Approval, Goal, Repository, Run, Task
from nexus.domain.enums import ApprovalState, GoalStatus, TaskStatus
from nexus.domain.transitions import assert_goal_transition
from nexus.policies.cost import DEFAULT_COST_POLICY
from nexus.services.events import record_audit
from nexus.services.orchestrator import cancel_run
from nexus.services.planner import DeterministicPlanner, create_plan
from nexus.workers.registry import get_registry

app = FastAPI(title="Nexus Control Plane", version=nexus.__version__)

# The dashboard runs on localhost:3400; keep CORS closed to local origins only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3400", "http://127.0.0.1:3400"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request, call_next):  # noqa: ANN001
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
            repository = Repository(name=payload.repository)
            db.add(repository)
            db.flush()
    goal = Goal(
        title=payload.title.strip(),
        description=payload.description.strip(),
        priority=payload.priority,
        autonomy=payload.autonomy,
        requested_worker=payload.requested_worker,
        acceptance_criteria=payload.acceptance_criteria,
        constraints=payload.constraints,
        repository_id=repository.id if repository else None,
    )
    db.add(goal)
    db.flush()
    create_plan(db, goal, DeterministicPlanner())
    record_audit(
        db,
        "goal.created",
        goal_id=goal.id,
        actor="owner",
        metadata={"requested_worker": payload.requested_worker},
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
def decide_approval(
    approval_id: str, payload: ApprovalDecisionIn, db: Session = Depends(get_db)
) -> OkOut:
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(404, "approval not found")
    if approval.state != ApprovalState.PENDING:
        raise HTTPException(409, f"approval already {approval.state}")
    approval.state = (
        ApprovalState.APPROVED if payload.decision == "approved" else ApprovalState.DENIED
    )
    approval.decided_at = datetime.now(UTC)
    approval.decision_note = payload.note
    record_audit(
        db,
        "approval.decided",
        actor="owner",
        status=str(approval.state),
        metadata={"approval_id": approval_id, "kind": approval.kind},
    )
    return OkOut(ok=True)
