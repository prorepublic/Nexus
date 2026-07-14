"""Independent cross-agent review (ADR-009).

After implementation and automated validation, a DIFFERENT worker reviews the
diff with read-only permissions and returns a structured verdict. The verdict
is parsed defensively (model output is untrusted input); findings persist to
review_findings and drive bounded repair.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from nexus.db.models import Goal, ReviewFinding, Task
from nexus.domain.enums import ReviewVerdict, TaskKind
from nexus.observability import get_logger
from nexus.workers.base import TaskSpec, WorkerAdapter

log = get_logger(__name__)

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approved", "changes-requested", "blocked"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "info"],
                    },
                    "category": {"type": "string"},
                    "description": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "blocking": {"type": "boolean"},
                },
                "required": ["severity", "description", "blocking"],
            },
        },
    },
    "required": ["verdict", "findings"],
}


@dataclass
class ReviewResult:
    verdict: ReviewVerdict
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    parse_error: str | None = None
    raw_ok: bool = True

    @property
    def blocking_findings(self) -> list[dict[str, Any]]:
        return [item for item in self.findings if item.get("blocking")]


def build_review_instruction(
    goal: Goal,
    task: Task,
    diff: str,
    changed: list[str],
    validation_summaries: list[str],
    baseline: str | None,
) -> str:
    criteria = "\n".join(f"- {c}" for c in (goal.acceptance_criteria or [])) or "- (none stated)"
    validations = "\n".join(f"- {v}" for v in validation_summaries) or "- (none)"
    diff_excerpt = diff[:60_000]
    return (
        "You are performing an independent code review of another agent's work.\n\n"
        f"# Goal\n{goal.title}\n\n{goal.description}\n\n"
        f"# Acceptance criteria\n{criteria}\n\n"
        f"# Task under review\n{task.title}\n\n{task.instruction[:4000]}\n\n"
        f"# Baseline commit\n{baseline or 'unknown'}\n\n"
        f"# Changed files\n" + "\n".join(f"- {f}" for f in changed[:100]) + "\n\n"
        f"# Automated validation results\n{validations}\n\n"
        f"# Diff\n```\n{diff_excerpt}\n```\n\n"
        "# Review checklist\n"
        "Check: acceptance criteria met; correctness; unintended scope; security "
        "(injection, secrets, unsafe subprocess/paths); tests present and honest; "
        "error handling; maintainability; backward compatibility.\n\n"
        "# Output format\n"
        "Respond with ONLY a JSON object matching this schema (no prose before or "
        "after):\n"
        f"{json.dumps(REVIEW_SCHEMA['properties'], indent=None)[:2000]}\n"
        'Example: {"verdict": "approved", "summary": "...", "findings": []}\n'
        "Use verdict 'changes-requested' when any blocking finding exists; "
        "'blocked' only when you cannot review (missing information)."
    )


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of possibly-noisy model output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except ValueError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def parse_review_output(text: str) -> ReviewResult:
    payload = extract_json_object(text)
    if payload is None:
        return ReviewResult(
            verdict=ReviewVerdict.BLOCKED,
            summary="reviewer did not return parseable JSON",
            parse_error="no JSON object found",
            raw_ok=False,
        )
    try:
        verdict = ReviewVerdict(str(payload.get("verdict", "")))
    except ValueError:
        return ReviewResult(
            verdict=ReviewVerdict.BLOCKED,
            summary="reviewer returned an unknown verdict",
            parse_error=f"invalid verdict: {payload.get('verdict')!r}",
            raw_ok=False,
        )
    findings_raw = payload.get("findings", [])
    findings: list[dict[str, Any]] = []
    for item in findings_raw if isinstance(findings_raw, list) else []:
        if not isinstance(item, dict) or not item.get("description"):
            continue
        severity = str(item.get("severity", "medium")).lower()
        if severity not in {"critical", "high", "medium", "low", "info"}:
            severity = "medium"
        findings.append(
            {
                "severity": severity,
                "category": str(item.get("category", "correctness"))[:60],
                "description": str(item["description"])[:4000],
                "file": str(item.get("file", ""))[:1000] or None,
                "line": str(item.get("line", ""))[:40] or None,
                "recommendation": str(item.get("recommendation", ""))[:4000],
                "blocking": bool(item.get("blocking", False)),
            }
        )
    # Consistency: changes-requested with zero blocking findings downgrades the
    # blocking bar to all findings; approved with blocking findings is refused.
    if verdict == ReviewVerdict.APPROVED and any(f["blocking"] for f in findings):
        verdict = ReviewVerdict.CHANGES_REQUESTED
    return ReviewResult(
        verdict=verdict, summary=str(payload.get("summary", ""))[:2000], findings=findings
    )


def persist_findings(
    session: Session,
    result: ReviewResult,
    task: Task,
    run_id: str | None,
    reviewer: str,
    source: str = "agent-review",
) -> None:
    for item in result.findings:
        session.add(
            ReviewFinding(
                task_id=task.id,
                run_id=run_id,
                reviewer=reviewer,
                severity=item["severity"],
                category=item["category"],
                description=item["description"],
                file=item.get("file"),
                line=item.get("line"),
                recommendation=item.get("recommendation", ""),
                blocking=item["blocking"],
                source=source,
            )
        )


def execute_review(
    adapter: WorkerAdapter,
    *,
    run_id: str,
    task: Task,
    goal: Goal,
    workspace: Path,
    diff: str,
    changed: list[str],
    validation_summaries: list[str],
    timeout_seconds: int,
    on_event=None,
) -> ReviewResult:
    instruction = build_review_instruction(
        goal, task, diff, changed, validation_summaries, task.baseline_commit
    )
    spec = TaskSpec(
        task_id=task.id,
        run_id=run_id,
        kind=TaskKind.REVIEW,
        instruction=instruction,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
        max_turns=15,
        read_only=True,
        context={"json_schema": REVIEW_SCHEMA},
    )
    worker_result = adapter.execute(spec, on_event=on_event)
    if not worker_result.ok:
        return ReviewResult(
            verdict=ReviewVerdict.BLOCKED,
            summary=f"reviewer run failed: {worker_result.summary[:300]}",
            parse_error=worker_result.error_category,
            raw_ok=False,
        )
    text = worker_result.output_text or worker_result.summary
    return parse_review_output(text)
