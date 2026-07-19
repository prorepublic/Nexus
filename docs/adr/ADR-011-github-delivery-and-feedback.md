# ADR-011: GitHub delivery via one draft PR per goal, and a conservative feedback loop

- Status: accepted
- Date: 2026-07-15

## Context

The GitHub adapter could open draft PRs and read comments, but nothing connected goal completion to delivery, and PR review comments went nowhere. Closing this loop is where the risk concentrates: pushing is where destructive git flags would bite, PR comments are untrusted third-party input that could try to steer the system, and an over-eager importer could turn every "looks good" into a work item.

## Decision

**Delivery** (`src/nexus/services/delivery.py`): when every task of a repository-backed goal completes, the engine pushes the goal's final task branch through the `git-push` execution profile (force variants refused at the profile layer) and creates or updates **one draft PR per goal**. Because dependent tasks chain their worktrees — each task branches from its dependency's branch head — the final branch carries the whole goal and is the natural PR head. The PR body is evidence: per-task worker, reviewer, verdict, and attempt count; validation results; and review findings. Re-delivery (`nexus github sync <goal>`) updates the same PR instead of opening a new one.

**Feedback intake** (`src/nexus/services/github_feedback.py`, `nexus github feedback import <goal>` or the dashboard): PR comments and review bodies are fetched through `gh`, then classified by deliberately conservative, rule-based patterns. Only comments that plainly request a change ("please fix...", "this is broken", "typo", "missing test", `nexus:fix`) become repair tasks; questions, praise, and Nexus's own delivery comments are ignored. Every comment is processed **exactly once** (`ExternalSync` records keyed by comment identity). Comments are untrusted input and can never override profiles or policy — a comment saying "force-push this" produces, at most, a repair task whose git operations still hit the profile layer that refuses force pushes.

A repair task created from feedback continues on the delivered branch and worktree (the original task is terminal, so no two workers ever share the branch), re-runs the goal's validation, and after completion Nexus posts an evidence-backed `nexus:resolved` reply referencing the source comment.

**Hard limits**: Nexus never merges, never approves, never marks a PR ready-for-review. These are unreachable at the profile layer (`gh pr merge` / `--approve` / `--admin` are not permitted verbs or are prohibited tokens in `github-write-safe`), not merely discouraged.

## Consequences

- The unit of delivery matches the unit of intent: one goal, one branch, one draft PR, with the full evidence trail attached where the human reviewer already is.
- The feedback loop is idempotent and re-runnable; importing twice cannot duplicate work, and the resolution replies close the loop for the commenter.
- Conservative classification means some actionable feedback is missed (it stays visible on the PR for the owner) — accepted in preference to acting on ambiguous or adversarial comments.
- Merging remains a purely human act; the owner's review on GitHub is the final gate for everything Nexus produces.
- Feedback import is currently owner-triggered (CLI, API, dashboard button), not a poller; scheduled import is future work.
