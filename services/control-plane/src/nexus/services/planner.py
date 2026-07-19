"""Goal planning (ADR-008).

Planner hierarchy:
- LivePlanner (default for real goals when a capable worker is available):
  a read-only Claude Code run produces a schema-validated structured plan.
  Invalid output gets one bounded repair attempt, then planning falls back.
- DeterministicPlanner (CI, tests, fallback): explicit rules, zero model usage.

Plans and their context are persisted in PostgreSQL; a conversational session
is never the system of record.
"""

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from nexus.config import get_settings
from nexus.db.models import ExecutionPlan, Goal, Repository, Task
from nexus.domain.enums import GoalStatus, Risk, TaskKind, TaskStatus, ValidationKind
from nexus.domain.transitions import assert_goal_transition
from nexus.observability import get_logger
from nexus.services.reviews import extract_json_object
from nexus.workers.base import TaskSpec, WorkerAdapter

log = get_logger(__name__)

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "exclusions": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "affected_components": {"type": "array", "items": {"type": "string"}},
        "definition_of_done": {"type": "string"},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [str(kind) for kind in TaskKind],
                    },
                    "instruction": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "integer"}},
                    "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                    "validations": {"type": "array", "items": {"type": "string"}},
                    "expected_files": {"type": "array", "items": {"type": "string"}},
                    "scope_globs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "kind", "instruction"],
            },
        },
    },
    "required": ["objective", "tasks"],
}


class PlanningError(Exception):
    pass


@dataclass
class PlannedTask:
    title: str
    instruction: str
    kind: TaskKind = TaskKind.IMPLEMENTATION
    risk: Risk = Risk.LOW
    depends_on_index: list[int] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    validations: list[str] = field(default_factory=list)
    scope_globs: list[str] = field(default_factory=list)


@dataclass
class PlanDraft:
    objective: str
    tasks: list[PlannedTask]
    assumptions: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    affected_components: list[str] = field(default_factory=list)
    definition_of_done: str = ""
    planner_name: str = "deterministic"


class Planner(Protocol):
    name: str

    def plan(self, goal: Goal, repo: Repository | None) -> PlanDraft: ...


def detect_cycles(tasks: list[PlannedTask]) -> None:
    """Reject dependency cycles and out-of-range references before persisting."""
    count = len(tasks)
    for index, task in enumerate(tasks):
        for dep in task.depends_on_index:
            if dep < 0 or dep >= count or dep == index:
                raise PlanningError(f"task {index} has invalid dependency index {dep}")
    visiting: set[int] = set()
    done: set[int] = set()

    def visit(node: int) -> None:
        if node in done:
            return
        if node in visiting:
            raise PlanningError(f"dependency cycle detected involving task {node}")
        visiting.add(node)
        for dep in tasks[node].depends_on_index:
            visit(dep)
        visiting.discard(node)
        done.add(node)

    for index in range(count):
        visit(index)


def normalize_plan_payload(payload: dict[str, Any]) -> PlanDraft:
    """Validate and normalize a structured plan from a live planner.

    Model output is untrusted: kinds, risks, validations, and dependency
    indices are all checked; anything invalid raises PlanningError so the
    caller can repair or fall back.
    """
    objective = str(payload.get("objective", "")).strip()
    raw_tasks = payload.get("tasks")
    if not objective or not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanningError("plan must contain an objective and at least one task")
    if len(raw_tasks) > 12:
        raise PlanningError("plan exceeds the 12-task bound")

    tasks: list[PlannedTask] = []
    for index, item in enumerate(raw_tasks):
        if not isinstance(item, dict):
            raise PlanningError(f"task {index} is not an object")
        title = str(item.get("title", "")).strip()
        instruction = str(item.get("instruction", "")).strip()
        if not title or not instruction:
            raise PlanningError(f"task {index} is missing title or instruction")
        try:
            kind = TaskKind(str(item.get("kind", "implementation")))
        except ValueError:
            raise PlanningError(f"task {index} has unknown kind {item.get('kind')!r}") from None
        try:
            risk = Risk(str(item.get("risk", "low")))
        except ValueError:
            risk = Risk.MEDIUM
        validations: list[str] = []
        for validation in item.get("validations", []) or []:
            try:
                validations.append(str(ValidationKind(str(validation))))
            except ValueError:
                log.warning("plan.unknown_validation", value=validation)
        depends = [int(dep) for dep in item.get("depends_on", []) or []]
        tasks.append(
            PlannedTask(
                title=title[:300],
                instruction=instruction[:20_000],
                kind=kind,
                risk=risk,
                depends_on_index=depends,
                expected_files=[str(f)[:500] for f in item.get("expected_files", []) or []][:50],
                validations=validations,
                scope_globs=[str(g)[:200] for g in item.get("scope_globs", []) or []][:20],
            )
        )
    detect_cycles(tasks)
    return PlanDraft(
        objective=objective[:4000],
        tasks=tasks,
        assumptions=[str(a)[:500] for a in payload.get("assumptions", []) or []][:20],
        exclusions=[str(e)[:500] for e in payload.get("exclusions", []) or []][:20],
        risks=[str(r)[:500] for r in payload.get("risks", []) or []][:20],
        affected_components=[str(c)[:300] for c in payload.get("affected_components", []) or []][
            :30
        ],
        definition_of_done=str(payload.get("definition_of_done", ""))[:2000],
    )


class DeterministicPlanner:
    """Rules-based planning: one implementation task per acceptance criterion
    (or a single task when none are given), followed by a review task."""

    name = "deterministic"

    def plan(self, goal: Goal, repo: Repository | None = None) -> PlanDraft:
        criteria: list[str] = [str(c) for c in (goal.acceptance_criteria or []) if str(c).strip()]
        constraints = [str(c) for c in (goal.constraints or []) if str(c).strip()]
        constraint_text = (
            "\nConstraints:\n" + "\n".join(f"- {c}" for c in constraints) if constraints else ""
        )
        default_validations = (
            [str(v) for v in ("lint", "tests")] if repo and repo.validation_profile else []
        )

        tasks: list[PlannedTask] = []
        if criteria:
            for index, criterion in enumerate(criteria):
                tasks.append(
                    PlannedTask(
                        title=f"Implement: {criterion[:120]}",
                        instruction=(
                            f"Goal: {goal.title}\n\n{goal.description}\n\n"
                            f"This task covers exactly one acceptance criterion:\n"
                            f"{criterion}{constraint_text}\n\n"
                            "Work only inside the assigned workspace. Do not touch "
                            "unrelated files."
                        ),
                        depends_on_index=[index - 1] if index > 0 else [],
                        validations=default_validations,
                    )
                )
        else:
            tasks.append(
                PlannedTask(
                    title=goal.title[:200],
                    instruction=(
                        f"Goal: {goal.title}\n\n{goal.description}{constraint_text}\n\n"
                        "Work only inside the assigned workspace."
                    ),
                    validations=default_validations,
                )
            )
        return PlanDraft(
            objective=f"Deliver: {goal.title.strip()}",
            tasks=tasks,
            assumptions=["Planned deterministically; no model was used for planning."],
            planner_name=self.name,
        )


class LivePlanner:
    """Structured planning through a read-only worker run (Claude Code by
    default). One bounded repair attempt on invalid output, then PlanningError
    so create_plan can fall back deterministically."""

    name = "live"

    def __init__(self, adapter: WorkerAdapter) -> None:
        self.adapter = adapter
        self.name = f"live:{adapter.name}"

    def _prompt(self, goal: Goal, repo: Repository | None, repair_note: str | None) -> str:
        criteria = "\n".join(f"- {c}" for c in (goal.acceptance_criteria or [])) or "- (none)"
        constraints = "\n".join(f"- {c}" for c in (goal.constraints or [])) or "- (none)"
        repo_block = ""
        if repo is not None:
            repo_block = (
                f"\n# Repository\nName: {repo.name}\n"
                f"Languages: {', '.join(str(x) for x in repo.languages) or 'unknown'}\n"
                f"Default branch: {repo.default_branch}\n"
                f"Available validations: {', '.join(sorted(repo.validation_profile or {}))}\n"
                f"Protected paths: {', '.join(str(x) for x in repo.protected_paths) or '(none)'}\n"
            )
        repair_block = (
            f"\n# Previous attempt was invalid\n{repair_note}\nFix the output.\n"
            if repair_note
            else ""
        )
        valid_kinds = ", ".join(str(kind) for kind in TaskKind)
        valid_validations = ", ".join(str(kind) for kind in ValidationKind)
        return (
            "You are the planning stage of Nexus, an autonomous development "
            "orchestrator. Convert the goal below into an execution plan of "
            "small, independently executable tasks.\n\n"
            f"# Goal\n{goal.title}\n\n{goal.description}\n\n"
            f"# Acceptance criteria\n{criteria}\n\n"
            f"# Constraints\n{constraints}\n"
            f"{repo_block}{repair_block}\n"
            "# Rules\n"
            "- 1 to 12 tasks; each bounded and testable; prefer fewer, well-scoped "
            "tasks.\n"
            "- depends_on uses zero-based indices into the tasks array; no cycles.\n"
            f"- kind must be one of: {valid_kinds}.\n"
            f"- validations entries must be from: {valid_validations}.\n"
            "- Include expected_files where outputs are predictable.\n"
            "- Do NOT include deployment, paid services, or actions outside the "
            "repository.\n\n"
            "# Output\n"
            "Respond with ONLY a JSON object matching this schema (no prose):\n"
            + json.dumps(PLAN_SCHEMA)[:3000]
        )

    def plan(self, goal: Goal, repo: Repository | None = None) -> PlanDraft:
        settings = get_settings()
        repair_note: str | None = None
        for attempt in (1, 2):
            with tempfile.TemporaryDirectory(prefix="nexus-planning-") as tmp:
                spec = TaskSpec(
                    task_id=f"planning-{goal.id}",
                    run_id=f"plan-{goal.id}-{attempt}",
                    kind=TaskKind.PLANNING,
                    instruction=self._prompt(goal, repo, repair_note)
                    + ("\n" + goal.description if False else ""),
                    workspace=Path(tmp),
                    timeout_seconds=settings.planning_timeout_seconds,
                    max_turns=10,
                    read_only=True,
                    context={"json_schema": PLAN_SCHEMA},
                )
                result = self.adapter.execute(spec)
            if not result.ok:
                raise PlanningError(f"planner run failed: {result.summary[:300]}")
            payload = extract_json_object(result.output_text or result.summary)
            if payload is None:
                repair_note = "output contained no parseable JSON object"
                continue
            try:
                draft = normalize_plan_payload(payload)
            except PlanningError as exc:
                repair_note = str(exc)
                continue
            draft.planner_name = self.name
            return draft
        raise PlanningError(f"planner produced invalid output twice: {repair_note}")


def select_planner(goal: Goal, registry=None) -> Planner:
    """auto: live Claude when available, deterministic otherwise."""
    from nexus.workers.registry import get_registry

    settings = get_settings()
    mode = (
        goal.plan_mode
        if goal.plan_mode in {"live", "deterministic"}
        else ("live" if settings.planner_mode in {"auto", "live"} else "deterministic")
    )
    if mode == "live":
        registry = registry or get_registry()
        from nexus.domain.enums import WorkerName
        from nexus.workers.registry import disabled_worker_names

        # Claude is the preferred live planner; any other available live worker
        # (e.g. Codex) is used when Claude is not authenticated or disabled.
        disabled = disabled_worker_names()
        for candidate in (WorkerName.CLAUDE_CODE, WorkerName.CODEX_CLI):
            if str(candidate) in disabled:
                continue
            adapter = registry.get(candidate)
            health = adapter.health_check()
            if health.installed and health.authenticated is not False:
                return LivePlanner(adapter)
        if goal.plan_mode == "live":
            raise PlanningError("live planning requested but no live planner is available")
        log.info("planner.fallback_deterministic", reason="no live planner available")
    return DeterministicPlanner()


def create_plan(
    session: Session, goal: Goal, planner: Planner, append_review: bool = True
) -> ExecutionPlan:
    """Plan a DRAFT goal: persists the plan and its tasks, moves goal to READY.

    A manual-autonomy goal additionally requires an 'approve-plan' approval
    before its tasks are enqueued (enforced in the queue, surfaced via API).
    """
    goal.status = assert_goal_transition(GoalStatus(goal.status), GoalStatus.PLANNING)
    repo = goal.repository

    try:
        draft = planner.plan(goal, repo)
    except PlanningError:
        if isinstance(planner, DeterministicPlanner):
            raise
        log.warning("planner.live_failed_falling_back", goal_id=goal.id)
        draft = DeterministicPlanner().plan(goal, repo)

    detect_cycles(draft.tasks)
    planned = list(draft.tasks)

    # Command-backed validation kinds the repository has no command for would
    # fail closed deterministically; drop them at plan time with an assumption
    # note instead of burning worker attempts on unwinnable validation.
    intrinsic = {"files-exist", "changed-scope", "secret-scan"}
    configured = set((repo.validation_profile or {}).keys()) if repo is not None else set()
    dropped: set[str] = set()
    for item in planned:
        keep = [v for v in item.validations if v in intrinsic or v in configured]
        dropped.update(set(item.validations) - set(keep))
        item.validations = keep
    if dropped:
        draft.assumptions.append(
            "Dropped unconfigured validation kinds from the plan: "
            + ", ".join(sorted(dropped))
            + " (configure with `nexus repo validation set` to enable them)."
        )
    if append_review and not any(task.kind == TaskKind.REVIEW for task in planned):
        # A read-only review task summarizing the goal-level outcome. Per-task
        # independent review runs inside the engine; this is the goal-level pass.
        planned.append(
            PlannedTask(
                title=f"Review delivered work for: {goal.title[:100]}",
                instruction=(
                    f"Review the changes produced for goal '{goal.title}' against its "
                    "acceptance criteria. Check correctness, unintended scope, security, "
                    "error handling, and maintainability. Report findings; do not modify "
                    "files."
                    + (
                        " " + goal.description[goal.description.find("[fake:review") :]
                        if "[fake:review" in goal.description
                        else ""
                    )
                ),
                kind=TaskKind.REVIEW,
                depends_on_index=list(range(len(planned))),
            )
        )

    plan = ExecutionPlan(
        goal_id=goal.id,
        objective=draft.objective,
        assumptions=draft.assumptions,
        proposed_solution=draft.definition_of_done
        or f"{len(planned)} task(s) derived from the goal definition.",
        affected_components=draft.affected_components,
        risks=draft.risks,
        validation_plan=sorted({v for task in planned for v in task.validations})
        or ["files-exist"],
        planner=draft.planner_name,
    )
    session.add(plan)
    session.flush()

    from nexus.db.models import TaskDependency

    created: list[Task] = []
    for index, item in enumerate(planned):
        idempotency = hashlib.sha256(f"{plan.id}:{index}:{item.title}".encode()).hexdigest()[:32]
        task = Task(
            goal_id=goal.id,
            plan_id=plan.id,
            title=item.title,
            instruction=item.instruction,
            kind=item.kind,
            risk=item.risk,
            status=TaskStatus.PENDING,
            expected_files=item.expected_files,
            validation_spec=item.validations or (["files-exist"] if item.expected_files else []),
            scope_globs=item.scope_globs,
            idempotency_key=idempotency,
        )
        session.add(task)
        created.append(task)
    session.flush()
    for item, task in zip(planned, created, strict=True):
        for dep_index in item.depends_on_index:
            session.add(TaskDependency(task_id=task.id, depends_on_task_id=created[dep_index].id))

    goal.status = assert_goal_transition(GoalStatus(goal.status), GoalStatus.READY)

    if goal.autonomy == "manual":
        from nexus.services.approvals import ApprovalPending, ensure_approval

        try:
            ensure_approval(
                session,
                "approve-plan",
                f"Plan for goal '{goal.title}': {len(created)} task(s), "
                f"objective: {draft.objective[:300]}",
                goal_id=goal.id,
                risk=Risk.LOW,
            )
        except ApprovalPending:
            pass  # expected: tasks stay un-enqueued until the owner approves
    return plan
