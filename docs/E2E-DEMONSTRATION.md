# End-to-End Demonstration (Development Automation V1)

A real development goal executed by Nexus on 2026-07-15 against a disposable
fixture repository. Every step below actually ran; identifiers are copied from
the audit trail so the run can be inspected (`nexus goal inspect`,
`nexus run inspect`, the dashboard, or the fixture PR).

## Setup

- Fixture repository: `prorepublic/nexus-e2e-fixture` (private, disposable) —
  a tiny Python project (`calculator.py` with `add()`, one pytest test).
- Registered with `nexus repo add ~/nexus-e2e-fixture` (detected python + uv,
  suggested a validation profile), trust raised with
  `nexus repo trust ... trusted-local`, validation profile set to
  `tests: uv run --group dev pytest -q`.
- Workers: Codex CLI 0.144.4 (installed by `nexus worker doctor
  --install-codex`, authenticated, live smoke test passed). Claude Code was
  disabled by the owner-facing `nexus worker disable claude-code` because its
  standalone CLI credentials are stale (see ROADMAP open items) — the router
  and planner honored the disable flag.

## The run

| Step | Evidence |
| --- | --- |
| Goal submitted via CLI with `--plan-mode live` | `goal_d73d8302c071413192a9` |
| Live AI planning (Codex, read-only sandbox, schema-validated JSON) | planner recorded as `live:codex-cli`; plan: "Implement subtract and unit coverage" -> "Run test suite" -> goal review; 76.5s planning run |
| Isolated worktree + deterministic branch | `nexus/d73d8302/f4e9622c-implement-subtract-and-u`, baseline `f27b8c43`, worktree under `~/.nexus/workspaces/` |
| Implementation routed to Codex (`--sandbox workspace-write`, cwd-confined) | run events + CommandExecution audit rows, profile `worker-codex` |
| Fail-closed validation caught a plan defect | the live plan requested an `acceptance` check with no configured command -> BLOCKED -> bounded repair engaged, budget exhausted after 3 attempts -> task `failed`, dependents cancelled, goal `failed` (finite autonomy, honestly reported) |
| Engine improved from the finding | deterministic config failures now fail fast without burning model attempts; unconfigured validation kinds are dropped at plan time with an assumption note |
| Operator escalation path | validation spec corrected, `nexus goal retry` requeued the failed task and restored cancelled dependents |
| Implementation completed | `subtract(a, b)` with type hints + docstring, `test_subtract` with positive and negative cases |
| Real validation evidence | `tests: passed (exit 0)` — `uv run --group dev pytest -q` executed by Nexus in the worktree via the validation-trusted profile; `changed-scope: passed` |
| Chained second task | "Run test suite" ran on branch `nexus/d73d8302/5531240e-run-test-suite` branched from the implementation branch head |
| Review stage | no independent reviewer available (Codex implemented; Claude disabled; the fake worker may not review real work) -> single-worker fallback recorded in the audit per `review_policy=preferred` |
| Safe push + draft PR created by Nexus | https://github.com/prorepublic/nexus-e2e-fixture/pull/1 (draft, evidence-backed body; git-push profile refuses force flags) |
| Actionable PR comment imported | classified actionable, repair task `task_be6e7ae0963e4edfa57b` created on the delivered branch |
| Feedback repair executed | Codex fixed the docstring (commit `952f2ee`), tests re-ran and passed, Nexus pushed the update to the same PR |
| Evidence-backed resolution | Nexus posted "Nexus pushed an update to this PR" and a `nexus:resolved` reply referencing the repair task and source comment |
| Idempotency | re-running the import: "0 actionable, 0 ignored, 3 already processed" (a process-randomized-hash bug found here was fixed with a stable SHA-256 digest) |
| Restart and recovery | `nexus start` (background, pid + logs) -> `/health` ok -> `nexus restart` -> `/health` ok -> `nexus stop` -> `nexus recover` (0 stale runs) |

## Not demonstrated live (credential-blocked)

- Claude Code as live planner/implementer/reviewer: the adapter is implemented,
  profile-enforced, and fixture-tested; the standalone CLI credentials are
  stale (401). After the owner re-runs `/login`, the identical flow routes
  planning and review to Claude with Codex implementing.
- Notion synchronization: the engine is implemented and tested against a fake
  Notion transport; it needs the owner's internal integration token
  (`nexus notion setup`) to run against the real workspace.

## Cost

All model work ran through the owner's existing ChatGPT Plus subscription via
the official Codex CLI. No paid API, hosting, or service was used or enabled.
