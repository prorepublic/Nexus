# Operating Model

The execution principles Nexus enforces in code. Where a principle is only partially wired up today, that is stated.

## 1. Outcome-driven loop

Work enters as a Goal (title, description, acceptance criteria, constraints) and flows through one loop:

Goal → Plan → Tasks → Queue → Execute → Validate → Repair → Review.

The owner defines the outcome and reviews the result. The system generates and manages everything in between. Goals settle to `review` (all tasks completed) or `failed`; nothing self-declares "done".

## 2. Finite autonomy

Every run is bounded by explicit numbers (defaults from `src/nexus/config.py`, environment-overridable via `NEXUS_*`):

| Limit | Default | Enforced in |
| --- | --- | --- |
| Repair attempts per task | `max_repair_attempts = 2` (so at most 3 attempts total) | orchestrator `_handle_failure` |
| Task timeout | `task_timeout_seconds = 1800` | adapter `execute` via `TaskSpec` |
| Validation command timeout | `command_timeout_seconds = 600` | `CommandPolicy` |
| Captured output cap | `max_output_bytes = 512000` (512 KB) | `CommandPolicy`; run events also size-capped |
| Worker turns | `max_turns = 30` per TaskSpec | Claude adapter `--max-turns` |

Beyond budget, a task fails terminally; dependents are cancelled so the goal settles honestly. Every state change goes through the hand-written transition tables (ADR-005); anything unlisted raises `InvalidTransition`. Runs are cancellable (`nexus run cancel`, API, dashboard).

## 3. Reversible defaults, decisions recorded

Default actions are reversible: branches instead of commits to main, draft PRs instead of merges, worktrees instead of in-place edits, additive migrations instead of destructive ones. Hard-to-reverse choices require an owner approval gate ([AUTONOMY-AND-APPROVALS.md](AUTONOMY-AND-APPROVALS.md)) and, when architectural, an ADR in [adr/](adr/).

## 4. Cross-agent review

The reviewer of a task always differs from its implementer when another worker is available: Claude implements → Codex reviews; Codex implements → Claude reviews. The fake worker never reviews real work. With a single worker available there is no reviewer, and that is recorded rather than faked. Review tasks run read-only. See [WORKER-ROUTING.md](WORKER-ROUTING.md).

## 5. Evidence-based completion

A worker claiming success is never trusted. Completion evidence is:

- **files-exist** — every expected file present in the workspace;
- **tests / lint / typecheck / build** — `make test|lint|typecheck|build` executed by the policy-constrained `CommandExecutor` when a Makefile is present (skipped, and recorded as skipped, when not);
- results persisted per run in `validation_results` and in the run's event timeline.

Only a fully passing validation set moves a task to `completed`. A failing set triggers the bounded repair loop, then terminal failure.

## 6. Git and workspace isolation rules

Implemented in `src/nexus/services/workspace.py` (module implemented and integration-tested; automatic wiring for repository-backed goals is scaffolded — see [ROADMAP.md](ROADMAP.md)):

1. Every implementation task runs in its own git worktree, never in the primary checkout.
2. Every task gets its own branch; deterministic naming `nexus/<goal8>/<task8>`.
3. Worktrees live under `~/.nexus/workspaces`, outside any repository.
4. The baseline commit is recorded before execution.
5. The full diff against baseline is captured after execution.
6. A worktree is removed only after its state is retained (diff artifact / commit).
7. Nexus refuses to operate on a dirty checkout it does not own.
8. Worktree removal is refused while uncommitted changes exist (unless the diff was captured first).
9. Never force-push; never touch protected branches.
10. Workers and validation commands are confined to the workspace (cwd enforcement in `CommandPolicy`; `--add-dir` / `--cd` in the adapters); paths escaping it are policy violations.

## 7. Communication rules

- Concise, factual status; no progress theater. State is inspectable at any time (`nexus status`, `nexus run inspect`, dashboard).
- Questions to the owner are consolidated and asked only when genuinely blocked or when an action is approval-gated. Routine reversible steps never interrupt.
- Failures are reported with the evidence (exit codes, validation summaries, event timeline), not adjectives.
- Nexus never claims numbers it cannot determine (e.g. monetary spend under subscription pricing — see [COST-GUARDRAILS.md](COST-GUARDRAILS.md)).
