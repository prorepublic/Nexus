# Architecture

Nexus is a monorepo with a Python control plane, a Next.js dashboard, and PostgreSQL as the single stateful dependency. Everything binds to localhost.

## Components

| Component | Location | Technology | Port |
| --- | --- | --- | --- |
| Control plane API | `services/control-plane/src/nexus/api` | Python 3.12, FastAPI, Pydantic | 127.0.0.1:8400 |
| Orchestrator | `src/nexus/services/orchestrator.py` | Thread inside `nexus start` | — |
| Work queue | `src/nexus/services/queue.py` | PostgreSQL `FOR UPDATE SKIP LOCKED` | — |
| Planner | `src/nexus/services/planner.py` | Deterministic rules (no model) | — |
| Workers | `src/nexus/workers/` | Claude Code CLI, Codex CLI, FakeWorker | — |
| Policies | `src/nexus/policies/` | approval, command, cost | — |
| Router | `src/nexus/routing/router.py` | Rules-based + cross-review | — |
| Adapters | `src/nexus/adapters/` | GitHub (gh CLI), Notion (REST), RDAP | — |
| CLI | `src/nexus/cli/` | typer (`uv run nexus ...`) | — |
| Web dashboard | `apps/web` | Next.js 15, TypeScript strict, Tailwind 4 | localhost:3400 |
| Database | `docker-compose.yml` | PostgreSQL 16-alpine, SQLAlchemy 2, Alembic | 127.0.0.1:5442 |
| Shared contracts | `packages/contracts` | Scaffold only (planned OpenAPI-generated TS types) | — |

## Data flow

```
Goal (owner, via CLI or dashboard)
  -> Execution Plan (DeterministicPlanner: one implementation task per
     acceptance criterion, sequentially dependent, plus a final read-only
     review task; live model-based planning is planned, not implemented)
  -> Tasks (pending -> ready -> queued once dependencies are satisfied;
     dependents of terminally failed tasks are cancelled so goals settle)
  -> Queue claim (SELECT ... FOR UPDATE SKIP LOCKED; claim + status flip to
     running are one transaction, so concurrent orchestrators never
     double-claim)
  -> Routing (rules-based preference per task kind; owner override via
     requested_worker; reviewer assigned cross-worker)
  -> Worker execution (adapter builds a bounded, non-interactive CLI
     invocation confined to the workspace; events streamed into run_events)
  -> Validation (evidence-based: files-exist checks plus make
     test/lint/typecheck/build when a Makefile is present; worker claims
     are never trusted)
  -> Repair (bounded: max_repair_attempts=2 by default, so at most 3
     attempts per task; failures beyond budget are terminal)
  -> Review (goal settles to `review` when every task completed, `failed`
     otherwise; owner completes or re-plans)
```

Every step writes audit_events; every run keeps an event timeline; cancellation is supported at run and goal level.

## State machines

Hand-written and explicit in `src/nexus/domain/transitions.py` (ADR-005). Illegal transitions raise `InvalidTransition`.

- **Goal:** draft → planning → ready → executing → blocked/review → completed | failed | cancelled (a failed goal may be re-planned).
- **Task:** pending → ready → queued → running → validating → repairing/review → completed | failed | cancelled (failed → queued only by explicit operator retry).
- **Run:** queued → running → succeeded | failed | cancelled | timed_out.

## Data model (19 tables)

Defined in `src/nexus/db/models.py`; one applied Alembic migration.

| Table | Purpose |
| --- | --- |
| users | Owner record (single-owner today) |
| repositories | Repositories goals can target |
| goals | Owner-submitted outcomes, status, autonomy, requested worker |
| execution_plans | Planner output per goal (objective, assumptions, validation plan) |
| tasks | Bounded units of work with kind, risk, instruction, expected files |
| task_dependencies | DAG edges gating enqueue |
| workers | Registered worker adapters and providers |
| worker_capabilities | Task kinds per worker |
| runs | One execution attempt of a task by a worker |
| run_events | Streamed worker/validation events per run (size-capped, redacted) |
| artifacts | Outputs retained from runs (diffs, reports) |
| approvals | Owner approval gates (pending/approved/denied/expired) |
| policies | Persisted policy records |
| prompts | Prompt records used for runs |
| command_executions | Audited subprocess executions |
| validation_results | Evidence per run (kind, passed, summary) |
| pull_requests | PRs opened for task branches |
| external_syncs | Idempotency records for Notion/GitHub mirrors |
| audit_events | Full audit trail of consequential actions |

## Worker adapter contract

`src/nexus/workers/base.py` (ADR-002). Provider-independent by construction: domain code sees only neutral types.

- `health_check() -> WorkerHealth` — installed/version/authenticated/detail; must report honestly (Claude auth is "unknown" until a live run; Codex reports not-installed on this machine).
- `capabilities() -> WorkerCapabilities` — supported task kinds.
- `estimate_suitability(kind) -> float` — 0.0 to 1.0; default derives from capabilities.
- `prepare_workspace(spec)` — provider-specific setup hook.
- `execute(spec, on_event) -> WorkerResult` — bounded by `spec.timeout_seconds`; streams `WorkerEvent`s.
- `cancel(run_id) -> bool` — best-effort cancellation.

Adapters: FakeWorker (deterministic, drives the whole loop with zero model usage), ClaudeCodeAdapter (`claude -p --output-format stream-json`, read-only vs write tool sets, `--add-dir` limited to the workspace, never `--dangerously-skip-permissions`), CodexCliAdapter (`codex exec --json`, `--sandbox read-only|workspace-write`, never `danger-full-access`). API-backed model adapters are planned and slot in behind the same contract.

## Workspace isolation

`src/nexus/services/workspace.py` is implemented and integration-tested standalone: git worktrees under `~/.nexus/workspaces`, deterministic branches `nexus/<goal8>/<task8>`, baseline commit recorded, diff capture, refusal to remove worktrees with uncommitted changes, never force-pushes. Honest note: the orchestrator currently uses scratch directories for fake-worker goals; automatic worktree creation for repository-backed goals is scaffolded ([ROADMAP.md](ROADMAP.md)).

## Technology choices and why

| Choice | Why | ADR |
| --- | --- | --- |
| Local-first control plane | Secrets and source stay on the owner's machine; hybrid later | [ADR-001](adr/ADR-001-local-first-hybrid-ready.md) |
| Subscription CLIs as workers | Uses what the owner already pays for; official interfaces; provider-neutral contract | [ADR-002](adr/ADR-002-cli-worker-adapters.md) |
| PostgreSQL as queue (no Redis) | One stateful dependency; transactional claims; audit locality | [ADR-003](adr/ADR-003-postgresql-work-queue.md) |
| Notion via REST internal integration | Works on the Free plan; no premium MCP dependency | [ADR-004](adr/ADR-004-notion-rest-integration.md) |
| Explicit state machines, no agent framework | Finite, auditable autonomy; frameworks only if they earn their keep | [ADR-005](adr/ADR-005-finite-autonomy.md) |
| FastAPI + SQLAlchemy 2 + Alembic + uv | Typed, migration-driven, fast local iteration | — |
| Next.js 15 dashboard, 5 s polling | Simple, no websocket infrastructure needed yet | — |

## Security surfaces

CORS restricted to localhost:3400; security headers (nosniff, DENY, no-referrer); no auth yet (planned; localhost-bound single-owner placeholder); secret redaction on every persistence path. See [THREAT-MODEL.md](THREAT-MODEL.md) and [../SECURITY.md](../SECURITY.md).
