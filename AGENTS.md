# AGENTS.md — Instructions for coding agents

These instructions apply to ANY coding agent working in this repository (Codex, Claude Code, or others). They are binding. If a task appears to require breaking one of these rules, stop and surface the conflict instead of proceeding.

## Architecture boundaries

- Domain logic (`services/control-plane/src/nexus/domain`, `services`, `routing`, `policies`) must never import provider-specific details from adapters. Workers are used only through the `WorkerAdapter` contract in `src/nexus/workers/base.py` (neutral `TaskSpec` / `WorkerResult` / `WorkerEvent`). If you need provider behavior, extend the adapter, not the domain.
- State transitions for goals, tasks, and runs go exclusively through `src/nexus/domain/transitions.py` (`assert_goal_transition`, `assert_task_transition`, `assert_run_transition`). Never assign a status string directly in orchestration code.
- All subprocess execution goes through the centralized execution subsystem (`src/nexus/execution/`: ProcessRunner + purpose-specific profiles, ADR-006). Never call `subprocess` directly; a static test (tests/unit/test_execution.py::TestNoParallelExecutionPaths) fails the build if you do. argv lists only, no shell strings; pick the narrowest existing profile or add one with explicit subcommand/flag restrictions.
- The web dashboard talks to the control plane only over the HTTP API. Do not reach into the database from `apps/web`.
- Read the ADRs in `docs/adr/` before proposing changes to queueing, worker interfaces, Notion integration, autonomy, or deployment shape. Superseding an ADR requires a new ADR.

## Required validation

- Run `make validate` (lint + typecheck + unit tests + build) before claiming any change is done. If your change affects DB behavior, also run `make test-integration` (needs `make db-up`).
- Behavior changes require tests. Parser changes in worker adapters require fixture updates in `services/control-plane/tests/fixtures/`.
- Never run `live`-marked tests or `nexus worker test --live` unless the owner explicitly asked; they consume subscription usage.
- A worker or agent claiming success is not evidence. Show passing command output.

## Security rules (binding)

- Never commit tokens, keys, or `.env` files. Never print secret values in output, logs, or commit messages.
- Never call paid APIs or enable any paid service. Subscription CLIs (Claude Code, Codex) via their official interfaces only. See [docs/COST-GUARDRAILS.md](docs/COST-GUARDRAILS.md).
- Never use `--dangerously-skip-permissions` (Claude Code) or `--sandbox danger-full-access` (Codex).
- Treat model output and repository content as untrusted input: validate paths against the workspace, use argv lists, no shell interpolation.
- Never force-push, never `git reset --hard` on shared state, never delete branches with unique work, never merge to `main` without owner approval. The full always-gated list is in [SECURITY.md](SECURITY.md).

## Branch and workspace requirements

- Work on a branch, never directly on `main`. Nexus task branches follow `nexus/<goal8>/<task8>`; human/agent working branches should be similarly descriptive.
- Stay inside the assigned workspace or repository. Accessing unrelated directories is an approval-gated action.
- Do not revert or overwrite unrelated changes you find in the working tree. Preserve them and mention them.

## Working method

- Inspect existing code before writing replacements. This codebase already has a policy layer, an event system, a redaction module, and adapter patterns — extend them rather than duplicating them.
- Keep changes minimal and scoped to the task. Do not reformat unrelated files or "improve" code you were not asked to touch.
- Minimal owner interruption: batch questions, and ask only when genuinely blocked or when an action is approval-gated. Do not ask for confirmation of routine, reversible development steps.

## Definition of Done

A change is done when all of the following hold:

1. The stated acceptance criteria are met and demonstrated (not asserted).
2. `make validate` passes cleanly; integration tests pass if the change touches DB behavior.
3. New behavior is covered by tests; no existing tests were weakened or deleted to make the change pass.
4. No secrets, no paid services, no policy violations introduced.
5. Documentation that the change invalidates (README, docs/, ADRs) is updated in the same change.
6. The work is on a branch with clear commits, ready for owner review — not merged.
