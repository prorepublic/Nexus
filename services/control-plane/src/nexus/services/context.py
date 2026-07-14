"""Durable context packaging for worker prompts (ADR-008).

Workers never rely on conversational history: everything they need is packaged
from PostgreSQL into the prompt, and every prompt is persisted with a manifest
describing exactly which context sources were included (auditability).
"""

from typing import Any

from sqlalchemy.orm import Session

from nexus.db.models import Goal, Prompt, Repository, Task
from nexus.observability import redact_text

PROMPT_VERSION = "v2"


def _repo_summary(repo: Repository | None) -> str:
    if repo is None:
        return ""
    lines = [f"Repository: {repo.name} (default branch: {repo.default_branch})"]
    if repo.languages:
        lines.append(f"Languages: {', '.join(str(item) for item in repo.languages)}")
    if repo.protected_paths:
        lines.append(
            "Protected paths (do NOT modify): "
            + ", ".join(str(item) for item in repo.protected_paths)
        )
    if repo.validation_profile:
        lines.append(
            "Validation that will run afterwards: " + ", ".join(sorted(repo.validation_profile))
        )
    return "\n".join(lines)


def _repair_context(task: Task) -> str:
    context: dict[str, Any] = task.context or {}
    sections: list[str] = []
    failures = context.get("failed_validations") or []
    if failures:
        sections.append(
            "Previous attempt failed validation:\n"
            + "\n".join(f"- {item}" for item in failures[:10])
        )
    findings = context.get("review_findings") or []
    if findings:
        sections.append(
            "Independent review requested changes. Address EXACTLY these findings:\n"
            + "\n".join(f"- {item}" for item in findings[:15])
        )
    pr_feedback = context.get("pr_feedback") or []
    if pr_feedback:
        sections.append(
            "Pull request feedback to address:\n"
            + "\n".join(f"- {item}" for item in pr_feedback[:10])
        )
    previous = context.get("attempt_history") or []
    if previous:
        sections.append("Attempt history:\n" + "\n".join(f"- {item}" for item in previous[-5:]))
    if sections:
        sections.append(
            "This is a bounded repair attempt "
            f"({task.attempt_count}/{task.max_attempts}). Preserve work that already "
            "passes; fix only what is listed above; do NOT broaden the scope."
        )
    return "\n\n".join(sections)


def build_task_instruction(
    session: Session, task: Task, goal: Goal, repo: Repository | None
) -> str:
    """Compose the full instruction for a worker run and persist it with its
    context manifest."""
    manifest: dict[str, Any] = {"sources": [], "prompt_version": PROMPT_VERSION}
    parts: list[str] = []

    parts.append(f"# Goal\n{goal.title}\n\n{goal.description}")
    manifest["sources"].append({"kind": "goal", "id": goal.id})

    criteria = [str(item) for item in (goal.acceptance_criteria or [])]
    if criteria:
        parts.append("# Acceptance criteria\n" + "\n".join(f"- {c}" for c in criteria))
    constraints = [str(item) for item in (goal.constraints or [])]
    if constraints:
        parts.append("# Constraints\n" + "\n".join(f"- {c}" for c in constraints))

    repo_summary = _repo_summary(repo)
    if repo_summary:
        parts.append("# Repository context\n" + repo_summary)
        manifest["sources"].append({"kind": "repository", "id": repo.id if repo else None})

    parts.append(f"# Your task\n{task.instruction}")
    manifest["sources"].append({"kind": "task", "id": task.id})

    if task.scope_globs:
        parts.append(
            "# Scope\nOnly modify files matching: " + ", ".join(str(g) for g in task.scope_globs)
        )

    repair = _repair_context(task)
    if repair:
        parts.append("# Repair context\n" + repair)
        manifest["sources"].append({"kind": "repair-context", "id": task.id})

    parts.append(
        "# Rules\n"
        "- Work only inside the current working directory (your task workspace).\n"
        "- Do not touch unrelated files, branches, or configuration.\n"
        "- Do not add paid services or new external dependencies without need.\n"
        "- Never print or store credentials.\n"
        "- Your claims are verified by independent validation afterwards; make "
        "the validation pass rather than describing success."
    )

    instruction = "\n\n".join(parts)
    session.add(
        Prompt(
            run_id=None,
            role="task",
            content=redact_text(instruction),
            context_manifest=manifest,
            version=PROMPT_VERSION,
        )
    )
    return instruction
