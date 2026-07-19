# ADR-008: Live AI planning with schema validation and deterministic fallback

- Status: accepted
- Date: 2026-07-15

## Context

The deterministic planner (one task per acceptance criterion) proved the loop but produces mechanical plans: it cannot decompose a goal it has not been told how to decompose, cannot assign per-task validations or scope, and cannot reason about the repository. Real goals need a model in the planning stage. But model output is untrusted input, planning must not require a conversational session (the system of record is PostgreSQL, not anyone's chat history), and planning must keep working when no live worker is available.

## Decision

Planning is a hierarchy (`src/nexus/services/planner.py`), selected per goal via `plan_mode` (`auto` default, `live`, `deterministic`):

- **`LivePlanner`** runs a read-only worker invocation (Claude Code preferred; Codex used when Claude is unavailable or disabled) in a throwaway temporary directory. The prompt embeds the goal, acceptance criteria, constraints, and registered-repository context (languages, default branch, available validations, protected paths), and demands a single JSON object against `PLAN_SCHEMA`: objective, assumptions, exclusions, risks, affected_components, definition_of_done, and 1 to 12 tasks, each with title, kind, instruction, dependencies (zero-based indices), risk, validations, expected_files, and scope_globs.
- **Model output is normalized defensively** (`normalize_plan_payload`): unknown task kinds are rejected, unknown validation kinds are dropped with a warning, all strings are length-capped, dependency indices are bounds-checked, and dependency cycles are rejected (`detect_cycles`). Invalid output earns exactly **one repair attempt** with the parse error quoted back; a second failure raises `PlanningError`.
- **Deterministic fallback**: when live planning fails (or no live worker is installed, authenticated, and enabled), `create_plan` falls back to the `DeterministicPlanner` — unless the owner forced `--plan-mode live`, in which case the failure is surfaced instead of silently downgraded.
- **Plans persist in PostgreSQL** (`execution_plans` with the planner recorded, tasks with per-task `validation_spec`, `expected_files`, `scope_globs`, and idempotency keys; dependencies in `task_dependencies`). Task prompts are packaged from the database by `services/context.py` and persisted to the `prompts` table with a **context manifest** recording exactly which sources (goal, repository, task, repair context) were included. Conversation history is never system memory.
- **Manual-autonomy gating**: a goal created with `--autonomy manual` gets an `approve-plan` approval; its tasks are not enqueued until the owner approves (`nexus goal approve-plan`, the dashboard plan card, or `POST /api/goals/{id}/approve-plan`). Denial cancels the goal.
- Unless the plan already contains one, a final read-only goal-level review task is appended, depending on all other tasks.

## Consequences

- Plans are real work breakdowns with per-task validation and scope, while remaining bounded: at most 12 tasks, no cycles, no unknown kinds, everything persisted and inspectable (`nexus goal inspect`, `GET /api/goals/{id}/plan`).
- A planning failure costs at most two read-only model runs and never corrupts state; the deterministic path keeps CI, tests, and degraded machines fully functional with zero model usage.
- The owner can hold the autonomy line at the plan boundary: with manual autonomy, nothing executes until the plan has been read and approved.
- The prompt manifest makes "what did the worker know" answerable after the fact — an auditability property conversational tools cannot offer.
- Planning consumes subscription usage when live; `plan_mode deterministic` remains available for cost-free operation.
