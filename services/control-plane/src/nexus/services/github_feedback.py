"""Pull request feedback intake (ADR-011).

Reads PR comments and review threads through the gh CLI, classifies each item,
converts actionable feedback into bounded repair tasks, and never processes
the same comment twice (cursor persisted as ExternalSync records).

Classification is deliberately conservative and rule-based: a comment becomes
a repair task only when it plainly asks for a change. Ambiguous discussion is
surfaced, not silently acted on — comments are untrusted input and never
override Nexus policies (a comment saying "force-push this" still hits the
profile layer that refuses force pushes).
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from nexus.adapters.github import GitHubAdapter
from nexus.db.models import (
    ExternalSync,
    Goal,
    PullRequestRecord,
    ReviewFinding,
    Task,
    utcnow,
)
from nexus.domain.enums import Risk, TaskKind, TaskStatus
from nexus.observability import get_logger
from nexus.services.events import record_audit

log = get_logger(__name__)

_ACTIONABLE_PATTERNS = [
    r"\bplease (fix|change|update|rename|remove|add|refactor|correct)\b",
    r"\b(should|must|needs? to) (be |use |have |include |return |handle )",
    r"\bcan you (fix|change|update|rename|remove|add)\b",
    r"\b(fix|correct) (this|the)\b",
    r"\bthis (is wrong|is broken|does ?n[o']t work|fails)\b",
    r"\btypo\b",
    r"\bmissing (test|docs?|error handling|validation)\b",
    r"\bnexus:(fix|change)\b",
]
_NON_ACTIONABLE_PATTERNS = [
    r"\b(lgtm|looks good|nice|great|thanks|thank you|\+1|ship it)\b",
    r"^\s*(why|how|what|when|is|are|does|do|did)\b.*\?\s*$",  # pure questions
    r"\bnexus pushed an update\b",  # our own delivery comments
    r"\bnexus:resolved\b",
]


@dataclass
class ClassifiedComment:
    external_id: str
    author: str | None
    body: str
    actionable: bool
    reason: str


def classify_comment(external_id: str, author: str | None, body: str) -> ClassifiedComment:
    text = (body or "").strip()
    lowered = text.lower()
    if not text:
        return ClassifiedComment(external_id, author, text, False, "empty")
    for pattern in _NON_ACTIONABLE_PATTERNS:
        if re.search(pattern, lowered):
            return ClassifiedComment(
                external_id, author, text, False, f"non-actionable pattern: {pattern}"
            )
    for pattern in _ACTIONABLE_PATTERNS:
        if re.search(pattern, lowered):
            return ClassifiedComment(external_id, author, text, True, f"matched: {pattern}")
    return ClassifiedComment(external_id, author, text, False, "no actionable signal")


def _already_processed(session: Session, external_id: str) -> bool:
    return (
        session.scalars(
            select(ExternalSync).where(
                ExternalSync.system == "github",
                ExternalSync.local_kind == "pr-comment",
                ExternalSync.local_key == external_id,
            )
        ).first()
        is not None
    )


def _mark_processed(session: Session, external_id: str, result: str) -> None:
    session.add(
        ExternalSync(
            system="github",
            local_kind="pr-comment",
            local_key=external_id,
            remote_id=external_id,
            remote_url=None,
            last_synced_at=utcnow(),
        )
    )
    log.info("github_feedback.processed", external_id=external_id, result=result)


@dataclass
class FeedbackReport:
    fetched: int = 0
    actionable: int = 0
    ignored: int = 0
    duplicates: int = 0
    repair_tasks: list[str] = field(default_factory=list)


def import_pr_feedback(
    session: Session, goal_id: str, github: GitHubAdapter | None = None
) -> FeedbackReport:
    """Fetch new PR comments for a goal, create repair tasks for actionable
    ones, and remember every processed comment."""
    report = FeedbackReport()
    goal = session.get(Goal, goal_id)
    if goal is None:
        raise ValueError("goal not found")
    pr = session.scalars(
        select(PullRequestRecord).where(PullRequestRecord.goal_id == goal_id)
    ).first()
    if pr is None or not pr.number:
        raise ValueError("goal has no pull request to import feedback from")
    github = github or GitHubAdapter(repo=pr.repository)

    comments = github.get_pr_review_comments(pr.number)
    final_task = session.scalars(
        select(Task)
        .where(Task.goal_id == goal_id, Task.branch.is_not(None))
        .order_by(Task.created_at.desc())
    ).first()

    for index, comment in enumerate(comments):
        body = str(comment.get("body", ""))
        author = comment.get("author")
        external_id = f"pr{pr.number}:{comment.get('id', index)}:{hash(body) & 0xFFFFFFFF:x}"
        report.fetched += 1
        if _already_processed(session, external_id):
            report.duplicates += 1
            continue
        classified = classify_comment(external_id, str(author) if author else None, body)
        if not classified.actionable:
            report.ignored += 1
            _mark_processed(session, external_id, "ignored")
            continue

        report.actionable += 1
        repair = Task(
            goal_id=goal.id,
            plan_id=final_task.plan_id if final_task else None,
            title=f"Address PR feedback: {body[:80]}",
            instruction=(
                f"Actionable feedback was posted on pull request #{pr.number} "
                f"({pr.url}).\n\nFeedback from {author or 'a reviewer'}:\n"
                f"{body[:3000]}\n\n"
                "Address exactly this feedback. Preserve all other behavior. "
                "Do not broaden the scope."
            ),
            kind=TaskKind.IMPLEMENTATION,
            risk=Risk.LOW,
            status=TaskStatus.PENDING,
            validation_spec=final_task.validation_spec if final_task else [],
            context={"pr_feedback": [body[:500]], "source_comment": external_id},
            # Continue in the delivered worktree/branch so the same PR updates.
            # The original task is terminal, so no two workers share the branch.
            branch=final_task.branch if final_task else None,
            worktree_path=final_task.worktree_path if final_task else None,
            baseline_commit=final_task.baseline_commit if final_task else None,
        )
        session.add(repair)
        session.flush()
        session.add(
            ReviewFinding(
                task_id=repair.id,
                run_id=None,
                reviewer=str(author or "pr-comment"),
                severity="medium",
                category="pr-feedback",
                description=body[:4000],
                blocking=True,
                source="pr-comment",
            )
        )
        _mark_processed(session, external_id, f"repair-task:{repair.id}")
        report.repair_tasks.append(repair.id)
        record_audit(
            session,
            "goal.pr-feedback-imported",
            goal_id=goal.id,
            task_id=repair.id,
            metadata={"pr": pr.number, "author": str(author or "")},
        )

    # Reopen the goal for execution when repairs were created.
    if report.repair_tasks:
        from nexus.domain.enums import GoalStatus
        from nexus.domain.transitions import assert_goal_transition

        if GoalStatus(goal.status) == GoalStatus.REVIEW:
            goal.status = assert_goal_transition(GoalStatus.REVIEW, GoalStatus.EXECUTING)
    return report


def reply_resolution(
    session: Session, goal_id: str, task: Task, github: GitHubAdapter | None = None
) -> None:
    """Post a concise evidence-backed resolution reply after a feedback repair."""
    pr = session.scalars(
        select(PullRequestRecord).where(PullRequestRecord.goal_id == goal_id)
    ).first()
    if pr is None or not pr.number:
        return
    github = github or GitHubAdapter(repo=pr.repository)
    source = (task.context or {}).get("source_comment", "")
    github.post_validation_summary(
        pr.number,
        f"nexus:resolved — task `{task.id}` addressed the feedback "
        f"({source}). Validation re-ran on the updated branch; see the PR diff "
        "for the change.",
    )
