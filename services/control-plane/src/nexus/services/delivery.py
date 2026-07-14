"""Goal delivery: safe push and draft pull request (ADR-011).

When every task of a repository-backed goal completes, Nexus pushes the final
task branch (git-push profile: force variants refused) and creates or updates
ONE draft PR per goal. Nexus never merges, never marks ready-for-review
without policy, and never force-pushes.
"""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus.adapters.github import GitHubAdapter, GitHubError
from nexus.db.models import (
    Goal,
    PullRequestRecord,
    ReviewFinding,
    Run,
    Task,
    ValidationResult,
)
from nexus.domain.enums import TaskStatus
from nexus.observability import get_logger
from nexus.services import workspace as ws
from nexus.services.events import record_audit

log = get_logger(__name__)


def _final_branch_task(session: Session, goal: Goal) -> Task | None:
    return session.scalars(
        select(Task)
        .where(
            Task.goal_id == goal.id,
            Task.status == TaskStatus.COMPLETED,
            Task.branch.is_not(None),
        )
        .order_by(Task.completed_at.desc())
    ).first()


def build_pr_body(session: Session, goal: Goal, tasks: list[Task]) -> str:
    lines = [
        "# Nexus goal delivery",
        "",
        f"**Goal:** {goal.title}",
        "",
        goal.description[:2000],
        "",
        "## Tasks",
    ]
    for task in tasks:
        reviewer = f", reviewed by {task.reviewer}" if task.reviewer else ""
        verdict = f" (review: {task.review_verdict})" if task.review_verdict else ""
        lines.append(
            f"- `{task.id}` {task.title[:100]} — {task.worker}{reviewer}{verdict}, "
            f"{task.attempt_count} attempt(s)"
        )
    lines.append("")
    lines.append("## Validation evidence")
    run_ids = [
        run.id for run in session.scalars(select(Run).where(Run.task_id.in_([t.id for t in tasks])))
    ]
    validations = session.scalars(
        select(ValidationResult).where(ValidationResult.run_id.in_(run_ids))
    ).all()
    if validations:
        for validation in validations[-20:]:
            lines.append(
                f"- {validation.kind}: **{validation.status}** — {validation.summary[:150]}"
            )
    else:
        lines.append("- (no command-backed validation was configured)")
    findings = session.scalars(
        select(ReviewFinding).where(ReviewFinding.task_id.in_([t.id for t in tasks]))
    ).all()
    if findings:
        lines.append("")
        lines.append("## Review findings")
        for finding in findings[-15:]:
            state = "resolved" if finding.resolved else ("blocking" if finding.blocking else "note")
            lines.append(f"- [{finding.severity}/{state}] {finding.description[:200]}")
    lines.append("")
    lines.append(
        f"Nexus goal: `{goal.id}` — created by the Nexus control plane. "
        "Draft PR: review before merging; Nexus never merges."
    )
    return "\n".join(lines)


def deliver_goal(session: Session, goal: Goal) -> PullRequestRecord | None:
    """Push the goal's final branch and create/update its draft PR."""
    repo = goal.repository
    if repo is None or not repo.local_path:
        return None
    if not repo.github_slug:
        record_audit(
            session,
            "goal.delivery-skipped",
            goal_id=goal.id,
            metadata={"reason": "repository has no GitHub remote"},
        )
        return None
    final_task = _final_branch_task(session, goal)
    if final_task is None or not final_task.worktree_path:
        record_audit(
            session,
            "goal.delivery-skipped",
            goal_id=goal.id,
            metadata={"reason": "no completed task branch to deliver"},
        )
        return None

    workspace = ws.Workspace(
        repo_path=Path(repo.local_path),
        worktree_path=Path(final_task.worktree_path),
        branch=final_task.branch or "",
        baseline_commit=final_task.baseline_commit or "",
    )
    ws.push_branch(workspace)
    record_audit(
        session, "goal.branch-pushed", goal_id=goal.id, metadata={"branch": workspace.branch}
    )

    tasks = list(
        session.scalars(select(Task).where(Task.goal_id == goal.id).order_by(Task.created_at))
    )
    body = build_pr_body(session, goal, tasks)
    github = GitHubAdapter(repo=repo.github_slug)

    existing = session.scalars(
        select(PullRequestRecord).where(PullRequestRecord.goal_id == goal.id)
    ).first()
    if existing is not None and existing.number:
        try:
            github.post_validation_summary(
                existing.number,
                "Nexus pushed an update to this PR.\n\n" + body[:8000],
            )
        except GitHubError as exc:
            log.warning("delivery.pr_comment_failed", error=str(exc)[:200])
        record_audit(
            session, "goal.pr-updated", goal_id=goal.id, metadata={"number": existing.number}
        )
        return existing

    ref = github.create_pull_request(
        title=f"Nexus: {goal.title[:100]}",
        body=body[:60_000],
        head=workspace.branch,
        base=repo.default_branch,
        draft=True,
    )
    record = PullRequestRecord(
        goal_id=goal.id,
        task_id=final_task.id,
        repository=repo.github_slug,
        number=ref.number,
        url=ref.url,
        branch=workspace.branch,
        state="draft",
    )
    session.add(record)
    record_audit(
        session, "goal.pr-created", goal_id=goal.id, metadata={"number": ref.number, "url": ref.url}
    )
    return record
