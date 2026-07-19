# ADR-009: Independent cross-agent review with contextual bounded repair

- Status: accepted
- Date: 2026-07-15

## Context

Automated validation proves that tests pass; it does not prove the change is correct, in scope, secure, or maintainable. The bootstrap design already assigned a cross-worker reviewer at routing time, but nothing executed a review, and repair attempts after failure re-ran the task with no information about what was wrong — the most expensive possible retry. A review stage only helps if its output is treated as untrusted input, its cost is bounded, and its findings actually reach the next attempt.

## Decision

After a task passes fail-closed validation (ADR-007), the engine runs an **independent review** (`src/nexus/services/reviews.py`, orchestrated in `_run_review`):

- A **different worker** than the implementer reviews the captured diff read-only, with the goal, acceptance criteria, task instruction, changed files, and automated validation results in the prompt. The fake worker can never review real workers' output (enforced in both the router and the engine's fallback selection).
- The reviewer must return a single JSON verdict against `REVIEW_SCHEMA`: `approved`, `changes-requested`, or `blocked`, plus findings (severity, category, description, file, line, recommendation, blocking flag). Output is parsed defensively; findings persist to the `review_findings` table.
- Consistency rules: `approved` with any blocking finding is downgraded to `changes-requested`. Unparseable output or a failed reviewer run is `blocked` and retried once (`max_review_attempts = 2`).
- **`changes-requested` triggers contextual repair**: the task re-queues within its bounded repair budget, and the repair prompt (`services/context.py`) carries the exact findings, any failed validations, the attempt history, and explicit scope-preservation instructions ("preserve work that already passes; fix only what is listed; do not broaden the scope").
- **Review policy** (`NEXUS_REVIEW_POLICY`): `required` — an implementation task cannot complete without a review verdict; when no independent reviewer exists (or review stays inconclusive), the engine creates a `single-worker-review-fallback` approval gate and blocks the task until the owner decides. `preferred` (default) — review runs whenever a distinct healthy worker exists; otherwise the single-worker fallback is recorded in the audit trail and the task proceeds. `disabled` — no agent review (validation and owner review only).
- Reviewed kinds are implementation, refactoring, testing, and maintenance. Review runs are recorded as `runs` with `purpose="review"`, so their cost and outcome are first-class.

## Consequences

- "It works" now requires a second model's structured dissent to be absent, at a bounded cost: at most two review attempts per task, and repair attempts still count against the same repair budget as validation failures.
- Repair is targeted rather than blind — the next attempt knows precisely which findings to fix — which measurably reduces wasted subscription usage on repeat mistakes.
- Every finding is persistent, attributable (reviewer, run, severity, blocking), and surfaced in the task detail (CLI, API, dashboard) and in the delivery PR body (ADR-011).
- Single-worker setups degrade honestly: the absence of independent review is either gated (`required`) or recorded (`preferred`), never faked.
- A dishonest or hallucinating reviewer is contained: verdicts outside the schema are `blocked`, not obeyed, and review runs are read-only so a reviewer cannot "fix" anything itself.
