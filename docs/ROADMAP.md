# Roadmap

Statuses are honest: **done** means implemented and tested in this repo today; **scaffolded** means the supporting pieces exist but the feature does not work end to end; **planned** means not started beyond design.

## Milestone 0 — Bootstrap foundation (this delivery)

| Item | Status |
| --- | --- |
| Monorepo layout, Makefile, docker-compose (Postgres 16, localhost:5442) | Done |
| Domain model: 19 tables, Alembic migration applied | Done |
| Explicit state machines for Goal/Task/Run (`InvalidTransition` on illegal moves) | Done |
| PostgreSQL work queue with `FOR UPDATE SKIP LOCKED`, dependency gating, honest goal settlement | Done |
| Orchestrator: dispatch, evidence-based validation, bounded repair (max 3 attempts/task), timeouts, cancellation, audit trail | Done |
| Deterministic planner (one task per acceptance criterion + review task) | Done |
| Worker contract + FakeWorker + ClaudeCodeAdapter + CodexCliAdapter | Done |
| Rules-based routing with cross-review and owner override | Done |
| Approval policy engine, approvals API, dashboard approvals page | Done |
| Command policy (allowlists, destructive-pattern refusal, env filtering, redaction) | Done |
| Cost policy (all paid services disabled) | Done |
| Git worktree isolation module (standalone, integration-tested) | Done |
| Validation runner (files-exist + make test/lint/typecheck/build) | Done |
| Structured logging with secret redaction | Done |
| GitHub adapter (issues, draft PRs, 17 labels, branch push) | Done |
| Notion adapter: `setup` + idempotent `bootstrap` (pages + 4 databases) | Done |
| CLI (doctor, bootstrap, start/stop/status, goal/task/run/worker, notion, domain) | Done |
| API (health, system status, goals, tasks, runs, approvals) + Next.js dashboard | Done |
| Domain discovery skill (`nexus domain search` via RDAP) | Done |
| Validation state: ruff clean, mypy clean (41 files), 137 tests passing, next build clean | Done |

## Milestone 1 — Close the execution loop for real repositories

| Item | Status |
| --- | --- |
| Wire git worktrees end to end for repository-backed goals (module is ready; orchestrator currently uses scratch dirs for fake-worker goals) | Scaffolded |
| Orchestrator-created approval gates mid-execution (policy engine + approvals surface exist; wiring into the execution path missing) | Scaffolded |
| Live Claude-based planning, opt-in (read-only planning task; structured plan ingestion from live output) | Planned |

## Milestone 2 — External surfaces

| Item | Status |
| --- | --- |
| Notion record sync: push goal/task state to the Goals/Tasks databases (`nexus notion sync` is a stub; ExternalSync table and `record_sync` upsert exist) | Scaffolded |
| GitHub PR loop: translate PR review comments into follow-up tasks (comment reading is implemented; translation is not) | Planned |
| GitHub Actions-based PR validation | Planned |
| OpenAPI-generated TypeScript contracts in `packages/contracts` | Planned |

## Milestone 3 — Hybrid remote monitoring

| Item | Status |
| --- | --- |
| Remote status/metadata surfaces via explicitly authorized runners; sensitive code and credentials never leave the local machine (design principle recorded in [ADR-001](adr/ADR-001-local-first-hybrid-ready.md)) | Planned |
| Background daemon mode for `nexus start` (foreground-only today) | Planned |
| API authentication (future Entra ID integration considered) | Planned |
| OS keychain for secrets (`.env` chmod 600 today) | Planned |

## Later

| Item | Status |
| --- | --- |
| API-backed model adapters (worker contract already isolates them; blocked on cost policy by design) | Planned |
| Voice interface | Planned |
| Personal-productivity modules | Planned |
