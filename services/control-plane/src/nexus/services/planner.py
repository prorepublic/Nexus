"""Goal planning.

Two planners exist:

- DeterministicPlanner (implemented, default): converts a goal into an
  execution plan and tasks using explicit rules. It requires no model usage,
  which keeps the vertical slice and all tests free and reproducible.
- Live planning through a worker (opt-in): a PLANNING task can be routed to
  Claude Code in read-only mode; its output is stored as a plan artifact for
  the owner to review. Structured plan ingestion from live output is
  scaffolded, not implemented (see docs/ROADMAP.md).
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from nexus.db.models import ExecutionPlan, Goal, Task
from nexus.domain.enums import GoalStatus, Risk, TaskKind, TaskStatus
from nexus.domain.transitions import assert_goal_transition


@dataclass
class PlannedTask:
    title: str
    instruction: str
    kind: TaskKind = TaskKind.IMPLEMENTATION
    risk: Risk = Risk.LOW
    depends_on_index: list[int] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)


class DeterministicPlanner:
    """Rules-based planning: one implementation task per acceptance criterion
    (or a single task when none are given), followed by a review task when the
    goal's autonomy allows it."""

    name = "deterministic"

    def plan(self, goal: Goal) -> tuple[str, list[PlannedTask]]:
        objective = f"Deliver: {goal.title.strip()}"
        criteria: list[str] = [str(c) for c in (goal.acceptance_criteria or []) if str(c).strip()]
        constraints = [str(c) for c in (goal.constraints or []) if str(c).strip()]
        constraint_text = (
            "\nConstraints:\n" + "\n".join(f"- {c}" for c in constraints) if constraints else ""
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
                )
            )

        review_deps = list(range(len(tasks)))
        tasks.append(
            PlannedTask(
                title=f"Review delivered work for: {goal.title[:100]}",
                instruction=(
                    f"Review the changes produced for goal '{goal.title}' against its "
                    "acceptance criteria. Check correctness, unintended scope, security, "
                    "error handling, and maintainability. Report findings; do not modify "
                    "files."
                ),
                kind=TaskKind.REVIEW,
                depends_on_index=review_deps,
            )
        )
        return objective, tasks


def create_plan(session: Session, goal: Goal, planner: DeterministicPlanner) -> ExecutionPlan:
    """Plan a DRAFT goal: persists the plan and its tasks, moves goal to READY."""
    goal.status = assert_goal_transition(GoalStatus(goal.status), GoalStatus.PLANNING)
    objective, planned = planner.plan(goal)

    plan = ExecutionPlan(
        goal_id=goal.id,
        objective=objective,
        assumptions=["Planned deterministically; no model was used for planning."],
        proposed_solution=f"{len(planned)} task(s) derived from the goal definition.",
        risks=[],
        validation_plan=["files-exist"],
        planner=planner.name,
    )
    session.add(plan)
    session.flush()

    from nexus.db.models import TaskDependency

    created: list[Task] = []
    for item in planned:
        task = Task(
            goal_id=goal.id,
            plan_id=plan.id,
            title=item.title,
            instruction=item.instruction,
            kind=item.kind,
            risk=item.risk,
            status=TaskStatus.PENDING,
            expected_files=item.expected_files,
        )
        session.add(task)
        created.append(task)
    session.flush()
    for item, task in zip(planned, created, strict=True):
        for dep_index in item.depends_on_index:
            session.add(TaskDependency(task_id=task.id, depends_on_task_id=created[dep_index].id))

    goal.status = assert_goal_transition(GoalStatus(goal.status), GoalStatus.READY)
    return plan
