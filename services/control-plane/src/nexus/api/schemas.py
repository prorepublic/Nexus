"""API request/response schemas. The web dashboard mirrors these types."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from nexus.domain.enums import Autonomy, Priority, WorkerName


class WorkerStatusOut(BaseModel):
    name: str
    provider: str
    available: bool
    version: str | None
    authenticated: bool | None
    detail: str


class CostModeOut(BaseModel):
    paid_apis_enabled: bool
    description: str


class SystemStatusOut(BaseModel):
    version: str
    database: str
    cost_mode: CostModeOut
    workers: list[WorkerStatusOut]


class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str
    database: Literal["ok", "unavailable"]


class GoalCreateIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=3, max_length=20_000)
    repository: str | None = Field(default=None, max_length=300)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    requested_worker: WorkerName | None = None
    priority: Priority = Priority.NORMAL
    autonomy: Autonomy = Autonomy.BOUNDED
    plan_mode: Literal["auto", "live", "deterministic"] = "auto"


class RepositoryCreateIn(BaseModel):
    source: str = Field(
        min_length=1, max_length=500, description="local path, owner/repo, or GitHub URL"
    )
    name: str | None = Field(default=None, max_length=300)


class TrustIn(BaseModel):
    level: Literal["untrusted", "reviewed", "trusted-local", "trusted-owner-approved"]


class RepositoryOut(BaseModel):
    id: str
    name: str
    local_path: str | None
    github_slug: str | None
    default_branch: str
    trust_level: str
    onboarded: bool
    languages: list[str]
    validation_kinds: list[str]


class PlanOut(BaseModel):
    id: str
    goal_id: str
    objective: str
    assumptions: list[str]
    risks: list[str]
    affected_components: list[str]
    validation_plan: list[str]
    planner: str
    proposed_solution: str


class ValidationResultOut(BaseModel):
    kind: str
    status: str
    summary: str
    exit_code: int | None
    duration_ms: int | None


class FindingOut(BaseModel):
    id: str
    severity: str
    category: str
    description: str
    file: str | None
    line: str | None
    recommendation: str
    blocking: bool
    resolved: bool
    source: str
    reviewer: str


class PullRequestOut(BaseModel):
    id: str
    goal_id: str | None
    repository: str
    number: int | None
    url: str | None
    branch: str
    state: str
    updated_at: datetime


class FeedbackReportOut(BaseModel):
    fetched: int
    actionable: int
    ignored: int
    duplicates: int
    repair_tasks: list[str]


class SettingsOut(BaseModel):
    review_policy: str
    planner_mode: str
    max_repair_attempts: int
    task_timeout_seconds: int
    lease_seconds: int
    cost_mode: "CostModeOut"


class GoalOut(BaseModel):
    id: str
    title: str
    description: str
    status: str
    priority: str
    autonomy: str
    repository: str | None
    requested_worker: str | None
    created_at: datetime
    updated_at: datetime


class TaskOut(BaseModel):
    id: str
    goal_id: str
    goal_title: str | None = None
    title: str
    status: str
    kind: str
    worker: str | None
    reviewer: str | None
    risk: str
    attempt_count: int
    branch: str | None
    created_at: datetime


class GoalDetailOut(GoalOut):
    tasks: list[TaskOut]


class TaskDetailOut(TaskOut):
    instruction: str
    worktree_path: str | None
    review_verdict: str | None
    validation_results: list["ValidationResultOut"]
    findings: list["FindingOut"]


class RunOut(BaseModel):
    id: str
    task_id: str
    task_title: str | None = None
    worker: str
    status: str
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    exit_summary: str | None


class RunEventOut(BaseModel):
    ts: datetime
    type: str
    status: str | None
    message: str


class RunDetailOut(RunOut):
    events: list[RunEventOut]


class ApprovalOut(BaseModel):
    id: str
    kind: str
    description: str
    risk: str
    state: str
    requested_at: datetime


class ApprovalDecisionIn(BaseModel):
    decision: Literal["approved", "denied"]
    note: str | None = Field(default=None, max_length=2000)


class OkOut(BaseModel):
    ok: bool


class ListOut[T](BaseModel):
    items: list[T]
