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
