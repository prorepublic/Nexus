"""One-way Notion synchronization for the Nexus control plane.

Authority rules:
- PostgreSQL is authoritative for live execution state (goals, tasks, runs,
  approvals, findings).
- GitHub is authoritative for code and pull request state.
- Notion is a human-readable view only. Sync is strictly one-way (database to
  Notion) in V1; changes made in Notion are never read back into execution
  state.

The sync is idempotent: every mirrored row is tracked in the external_syncs
table (system="notion"), so reruns update existing Notion pages instead of
creating duplicates. A failure on one entity is recorded in the report and
does not abort the rest of the sync. All free-text content is passed through
nexus.observability.redact_text so secrets never reach Notion.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus.adapters.notion import (
    NotionClient,
    NotionError,
    bootstrap_workspace,
    record_sync,
)
from nexus.db.models import (
    Approval,
    ExecutionPlan,
    ExternalSync,
    Goal,
    PullRequestRecord,
    Repository,
    ReviewFinding,
    Run,
    Task,
    utcnow,
)
from nexus.observability import get_logger, redact_text

log = get_logger(__name__)

SYNC_SYSTEM = "notion"
# Notion caps rich_text content at 2000 characters; stay under with margin.
MAX_TEXT = 1900
# Runs are high-volume; only the most recent ones are mirrored.
MAX_RUNS = 50


@dataclass
class SyncReport:
    """Outcome of one sync_all pass, keyed by entity kind (e.g. "goal")."""

    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    errors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_created(self) -> int:
        return sum(self.created.values())

    @property
    def total_updated(self) -> int:
        return sum(self.updated.values())

    @property
    def total_errors(self) -> int:
        return sum(len(msgs) for msgs in self.errors.values())


# -- property builders --------------------------------------------------------
def _text(value: str | None) -> str:
    """Redact and truncate free text destined for Notion."""
    return redact_text(value or "")[:MAX_TEXT]


def _title(value: str | None) -> dict[str, Any]:
    return {"title": [{"text": {"content": _text(value)}}]}


def _rich(value: str | None) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": _text(value)}}]}


def _select(value: str | None) -> dict[str, Any]:
    return {"select": {"name": str(value)}}


def _date(value: datetime) -> dict[str, Any]:
    return {"date": {"start": value.isoformat()}}


def _url(value: str) -> dict[str, Any]:
    return {"url": value}


def _checkbox(value: bool) -> dict[str, Any]:
    return {"checkbox": bool(value)}


def _repository_props(repo: Repository) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Name": _title(repo.name),
        "Repo ID": _rich(repo.id),
        "Trust": _select(repo.trust_level),
        "Default Branch": _rich(repo.default_branch),
    }
    if repo.local_path:
        props["Local Path"] = _rich(repo.local_path)
    if repo.remote_url:
        props["GitHub Link"] = _url(repo.remote_url)
    if repo.updated_at:
        props["Updated"] = _date(repo.updated_at)
    return props


def _goal_props(goal: Goal) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Title": _title(goal.title),
        "Goal ID": _rich(goal.id),
        "Description": _rich(goal.description),
        "Status": _select(goal.status),
        "Priority": _select(goal.priority),
    }
    if goal.repository is not None:
        props["Repository"] = _rich(goal.repository.name)
    elif goal.repository_id:
        props["Repository"] = _rich(goal.repository_id)
    if goal.requested_worker:
        props["Requested Worker"] = _select(goal.requested_worker)
    if goal.created_at:
        props["Created"] = _date(goal.created_at)
    if goal.updated_at:
        props["Updated"] = _date(goal.updated_at)
    return props


def _plan_props(plan: ExecutionPlan) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Objective": _title(plan.objective),
        "Plan ID": _rich(plan.id),
        "Goal": _rich(plan.goal_id),
        "Planner": _rich(plan.planner),
    }
    if plan.created_at:
        props["Created"] = _date(plan.created_at)
    return props


def _task_props(task: Task) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Title": _title(task.title),
        "Task ID": _rich(task.id),
        "Goal": _rich(task.goal_id),
        "Status": _select(task.status),
        "Risk": _select(task.risk),
        "Attempt Count": {"number": task.attempt_count},
    }
    if task.worker:
        props["Worker"] = _rich(task.worker)
    if task.reviewer:
        props["Reviewer"] = _rich(task.reviewer)
    if task.branch:
        props["Branch"] = _rich(task.branch)
    if task.started_at:
        props["Started"] = _date(task.started_at)
    if task.completed_at:
        props["Completed"] = _date(task.completed_at)
    return props


def _run_props(run: Run) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Run ID": _title(run.id),
        "Task": _rich(run.task_id),
        "Worker": _select(run.worker),
        "Status": _select(run.status),
        "Purpose": _select(run.purpose),
    }
    if run.started_at:
        props["Started"] = _date(run.started_at)
    if run.finished_at:
        props["Finished"] = _date(run.finished_at)
    if run.exit_summary:
        props["Summary"] = _rich(run.exit_summary)
    return props


def _approval_props(approval: Approval) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Description": _title(approval.description),
        "Approval ID": _rich(approval.id),
        "Kind": _rich(approval.kind),
        "Risk": _select(approval.risk),
        "State": _select(approval.state),
    }
    if approval.created_at:
        props["Requested"] = _date(approval.created_at)
    if approval.decided_at:
        props["Decided"] = _date(approval.decided_at)
    return props


def _pull_request_props(pr: PullRequestRecord) -> dict[str, Any]:
    title = f"{pr.repository} #{pr.number}" if pr.number is not None else pr.branch
    props: dict[str, Any] = {
        "Title": _title(title),
        "PR ID": _rich(pr.id),
        "Repository": _rich(pr.repository),
        "Branch": _rich(pr.branch),
        "State": _select(pr.state),
    }
    if pr.url:
        props["URL"] = _url(pr.url)
    if pr.goal_id:
        props["Goal"] = _rich(pr.goal_id)
    if pr.updated_at:
        props["Updated"] = _date(pr.updated_at)
    return props


def _finding_props(finding: ReviewFinding) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Description": _title(finding.description),
        "Finding ID": _rich(finding.id),
        "Task": _rich(finding.task_id),
        "Severity": _select(finding.severity),
        "Category": _rich(finding.category),
        "Blocking": _checkbox(finding.blocking),
        "Resolved": _checkbox(finding.resolved),
        "Source": _select(finding.source),
    }
    if finding.file:
        props["File"] = _rich(finding.file)
    return props


# -- sync engine ---------------------------------------------------------------
def _find_sync(session: Session, local_kind: str, local_key: str) -> ExternalSync | None:
    return session.scalars(
        select(ExternalSync).where(
            ExternalSync.system == SYNC_SYSTEM,
            ExternalSync.local_kind == local_kind,
            ExternalSync.local_key == local_key,
        )
    ).first()


def _sync_rows(
    session: Session,
    client: NotionClient,
    report: SyncReport,
    kind: str,
    database_id: str,
    rows: Sequence[Any],
    build_props: Callable[[Any], dict[str, Any]],
) -> None:
    """Upsert one entity kind. Individual row failures are collected, not raised."""
    for row in rows:
        try:
            properties = build_props(row)
            existing = _find_sync(session, kind, row.id)
            if existing is not None:
                client.update_page(existing.remote_id, properties)
                existing.last_synced_at = utcnow()
                report.updated[kind] = report.updated.get(kind, 0) + 1
            else:
                page_id = client.create_db_page(database_id, properties)
                record_sync(session, SYNC_SYSTEM, kind, row.id, page_id)
                report.created[kind] = report.created.get(kind, 0) + 1
            session.flush()
        except Exception as exc:  # noqa: BLE001 - one row must not abort the sync
            message = f"{row.id}: {exc}"
            report.errors.setdefault(kind, []).append(message)
            log.warning("notion.sync.entity-failed", kind=kind, error=str(exc))


def sync_all(session: Session, client: NotionClient, parent_page: str) -> SyncReport:
    """Mirror control-plane state to the Nexus Notion workspace. Safe to rerun.

    Resolves the workspace via bootstrap_workspace (idempotent: existing pages
    and databases are looked up by title, never duplicated), then upserts each
    entity kind in dependency order. Existing Notion pages are found through
    the external_syncs table and updated in place; new rows create pages and
    record a sync entry. Successful syncs are committed even when other rows
    fail, so a rerun heals partial failures.
    """
    report = SyncReport()
    bootstrap = bootstrap_workspace(client, parent_page)
    home_id = bootstrap.home_page_id
    if home_id is None:
        raise NotionError("workspace bootstrap did not resolve a Nexus Home page")

    specs: list[tuple[str, str, Sequence[Any], Callable[[Any], dict[str, Any]]]] = [
        (
            "repository",
            "Repositories",
            session.scalars(select(Repository).order_by(Repository.created_at)).all(),
            _repository_props,
        ),
        (
            "goal",
            "Goals",
            session.scalars(select(Goal).order_by(Goal.created_at)).all(),
            _goal_props,
        ),
        (
            "plan",
            "Plans",
            session.scalars(select(ExecutionPlan).order_by(ExecutionPlan.created_at)).all(),
            _plan_props,
        ),
        (
            "task",
            "Tasks",
            session.scalars(select(Task).order_by(Task.created_at)).all(),
            _task_props,
        ),
        (
            "run",
            "Runs",
            session.scalars(select(Run).order_by(Run.created_at.desc()).limit(MAX_RUNS)).all(),
            _run_props,
        ),
        (
            "approval",
            "Approvals",
            session.scalars(select(Approval).order_by(Approval.created_at)).all(),
            _approval_props,
        ),
        (
            "pull-request",
            "Pull Requests",
            session.scalars(select(PullRequestRecord).order_by(PullRequestRecord.created_at)).all(),
            _pull_request_props,
        ),
        (
            "review-finding",
            "Review Findings",
            session.scalars(select(ReviewFinding).order_by(ReviewFinding.created_at)).all(),
            _finding_props,
        ),
    ]

    for kind, db_title, rows, build_props in specs:
        if not rows:
            continue
        database_id = client.find_child_by_title(home_id, db_title, "child_database")
        if database_id is None:
            report.errors.setdefault(kind, []).append(
                f"database {db_title!r} not found under Nexus Home"
            )
            continue
        _sync_rows(session, client, report, kind, database_id, rows, build_props)

    session.commit()
    log.info(
        "notion.sync.completed",
        created=report.total_created,
        updated=report.total_updated,
        errors=report.total_errors,
    )
    return report
