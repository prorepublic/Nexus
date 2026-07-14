"""SQLAlchemy models for the Nexus control plane.

PostgreSQL is the system of record for live execution state. GitHub remains
authoritative for code and pull requests; Notion is a human-readable surface.
Event payloads are redacted before persistence (nexus.observability).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nexus.db.base import Base
from nexus.domain.enums import (
    ApprovalState,
    Autonomy,
    GoalStatus,
    Priority,
    Risk,
    RunStatus,
    TaskStatus,
)
from nexus.ids import new_id


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(Base, TimestampMixin):
    """Single-owner for now; local auth placeholder designed for future Entra ID."""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("user"))
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), default="owner")


class Repository(Base, TimestampMixin):
    __tablename__ = "repositories"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("repo"))
    name: Mapped[str] = mapped_column(String(300), unique=True)  # e.g. owner/repo or local path
    local_path: Mapped[str | None] = mapped_column(String(1000))
    remote_url: Mapped[str | None] = mapped_column(String(500))
    github_slug: Mapped[str | None] = mapped_column(String(300))  # owner/repo
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    trust_level: Mapped[str] = mapped_column(String(30), default="untrusted")
    validation_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    languages: Mapped[list[Any]] = mapped_column(JSON, default=list)
    package_managers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    protected_paths: Mapped[list[Any]] = mapped_column(JSON, default=list)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    last_inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Goal(Base, TimestampMixin):
    __tablename__ = "goals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("goal"))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=GoalStatus.DRAFT, index=True)
    priority: Mapped[str] = mapped_column(String(10), default=Priority.NORMAL)
    autonomy: Mapped[str] = mapped_column(String(10), default=Autonomy.BOUNDED)
    repository_id: Mapped[str | None] = mapped_column(ForeignKey("repositories.id"))
    requested_worker: Mapped[str | None] = mapped_column(String(40))
    acceptance_criteria: Mapped[list[Any]] = mapped_column(JSON, default=list)
    constraints: Mapped[list[Any]] = mapped_column(JSON, default=list)
    plan_mode: Mapped[str] = mapped_column(String(20), default="auto")  # auto|live|manual
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))

    repository: Mapped[Repository | None] = relationship()
    plan: Mapped["ExecutionPlan | None"] = relationship(back_populates="goal", uselist=False)
    tasks: Mapped[list["Task"]] = relationship(back_populates="goal")


class ExecutionPlan(Base, TimestampMixin):
    __tablename__ = "execution_plans"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("plan"))
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), unique=True)
    objective: Mapped[str] = mapped_column(Text)
    assumptions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    proposed_solution: Mapped[str] = mapped_column(Text, default="")
    affected_components: Mapped[list[Any]] = mapped_column(JSON, default=list)
    risks: Mapped[list[Any]] = mapped_column(JSON, default=list)
    validation_plan: Mapped[list[Any]] = mapped_column(JSON, default=list)
    planner: Mapped[str] = mapped_column(String(40), default="deterministic")

    goal: Mapped[Goal] = relationship(back_populates="plan")


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("task"))
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("execution_plans.id"))
    title: Mapped[str] = mapped_column(String(300))
    instruction: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="implementation")
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.PENDING, index=True)
    risk: Mapped[str] = mapped_column(String(10), default=Risk.LOW)
    worker: Mapped[str | None] = mapped_column(String(40))  # selected worker
    reviewer: Mapped[str | None] = mapped_column(String(40))
    branch: Mapped[str | None] = mapped_column(String(300))
    worktree_path: Mapped[str | None] = mapped_column(String(1000))
    baseline_commit: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    validation_spec: Mapped[list[Any]] = mapped_column(JSON, default=list)
    expected_files: Mapped[list[Any]] = mapped_column(JSON, default=list)
    scope_globs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # repair context
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    review_verdict: Mapped[str | None] = mapped_column(String(30))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    goal: Mapped[Goal] = relationship(back_populates="tasks")
    runs: Mapped[list["Run"]] = relationship(back_populates="task")

    __table_args__ = (Index("ix_tasks_status_created", "status", "created_at"),)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    depends_on_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)


class Worker(Base, TimestampMixin):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("wrk"))
    name: Mapped[str] = mapped_column(String(40), unique=True)  # claude-code | codex-cli | fake
    provider: Mapped[str] = mapped_column(String(60))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cost_mode: Mapped[str] = mapped_column(String(40), default="subscription")
    last_health: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerCapability(Base):
    __tablename__ = "worker_capabilities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_name: Mapped[str] = mapped_column(String(40), index=True)
    capability: Mapped[str] = mapped_column(String(60))  # TaskKind values
    weight: Mapped[int] = mapped_column(Integer, default=1)


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("run"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    worker: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    session_ref: Mapped[str | None] = mapped_column(String(200))  # worker session/thread id
    exit_code: Mapped[int | None] = mapped_column(Integer)
    exit_summary: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(60))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    purpose: Mapped[str] = mapped_column(String(30), default="implementation")  # |review|planning
    orchestrator_id: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    task: Mapped[Task] = relationship(back_populates="runs")
    events: Mapped[list["RunEvent"]] = relationship(back_populates="run")


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str | None] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text, default="")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    run: Mapped[Run] = relationship(back_populates="events")


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("art"))
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # plan|prompt|diff|report|log|decision|file
    name: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, default="")  # size-capped, redacted
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("appr"))
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    kind: Mapped[str] = mapped_column(String(80))  # e.g. merge-to-main, paid-service
    description: Mapped[str] = mapped_column(Text)
    risk: Mapped[str] = mapped_column(String(10), default=Risk.MEDIUM)
    state: Mapped[str] = mapped_column(String(20), default=ApprovalState.PENDING, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)


class Policy(Base, TimestampMixin):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pol"))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(40))  # routing|approval|command|cost
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Prompt(Base, TimestampMixin):
    __tablename__ = "prompts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("prm"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="task")
    content: Mapped[str] = mapped_column(Text)  # redacted before persistence
    # What context was packaged into this prompt and where it came from.
    context_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[str] = mapped_column(String(20), default="v1")


class CommandExecution(Base):
    __tablename__ = "command_executions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    argv: Mapped[list[Any]] = mapped_column(JSON, default=list)
    cwd: Mapped[str] = mapped_column(String(1000))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    output_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_decision: Mapped[str] = mapped_column(String(20), default="allowed")


class ReviewFinding(Base, TimestampMixin):
    __tablename__ = "review_findings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("find"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(20))  # critical|high|medium|low|info
    category: Mapped[str] = mapped_column(String(60), default="correctness")
    description: Mapped[str] = mapped_column(Text)
    file: Mapped[str | None] = mapped_column(String(1000))
    line: Mapped[str | None] = mapped_column(String(40))  # line or range
    recommendation: Mapped[str] = mapped_column(Text, default="")
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(30), default="agent-review")  # |pr-comment


class ValidationResult(Base, TimestampMixin):
    __tablename__ = "validation_results"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("val"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # ValidationKind
    status: Mapped[str] = mapped_column(String(20), default="failed")  # ValidationStatus
    passed: Mapped[bool] = mapped_column(Boolean)
    summary: Mapped[str] = mapped_column(Text, default="")
    command: Mapped[list[Any]] = mapped_column(JSON, default=list)
    profile: Mapped[str] = mapped_column(String(60), default="")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    output_tail: Mapped[str] = mapped_column(Text, default="")


class PullRequestRecord(Base, TimestampMixin):
    __tablename__ = "pull_requests"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pr"))
    goal_id: Mapped[str | None] = mapped_column(ForeignKey("goals.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    repository: Mapped[str] = mapped_column(String(300))
    number: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String(500))
    branch: Mapped[str] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(30), default="draft")


class ExternalSync(Base, TimestampMixin):
    """Tracks Nexus entities mirrored to external systems (Notion, GitHub)."""

    __tablename__ = "external_syncs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("sync"))
    system: Mapped[str] = mapped_column(String(30), index=True)  # notion|github
    local_kind: Mapped[str] = mapped_column(String(40))  # goal|task|adr|workspace-page
    local_key: Mapped[str] = mapped_column(String(200))
    remote_id: Mapped[str] = mapped_column(String(200))
    remote_url: Mapped[str | None] = mapped_column(String(500))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_external_syncs_lookup", "system", "local_kind", "local_key", unique=True),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(60), index=True)
    goal_id: Mapped[str | None] = mapped_column(String(40))
    task_id: Mapped[str | None] = mapped_column(String(40))
    run_id: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(60), default="system")
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str | None] = mapped_column(String(30))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
