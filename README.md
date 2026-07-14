# Nexus

Nexus is a secure, local-first, provider-independent AI development orchestration platform. The owner submits a high-level development Goal; Nexus plans it into Tasks, routes each Task to a Worker (Claude Code CLI, Codex CLI, or a deterministic fake worker), executes it in isolation, validates the result with evidence, repairs within bounded attempts, and surfaces the outcome for human review.

**Status: bootstrap foundation.** The core control plane, domain model, orchestration loop, worker adapters, policies, CLI, API, and dashboard are implemented and covered by 137 passing tests (112 unit, 25 integration). Several integrations are scaffolded or planned; the table below and [docs/ROADMAP.md](docs/ROADMAP.md) state exactly what works today.

## Architecture

```
                        +---------------------------+
   Owner                |  Web Dashboard (Next.js)  |
   (goals, approvals)   |  localhost:3400           |
        |               +------------+--------------+
        |                            | HTTP (localhost only)
        v                            v
  +-----------+          +---------------------------+
  | nexus CLI |--------->|  Control Plane (FastAPI)  |
  +-----------+          |  localhost:8400           |
                         +------------+--------------+
                                      |
                    +-----------------+------------------+
                    |        Orchestrator loop           |
                    |  plan -> queue -> execute ->       |
                    |  validate -> repair -> review      |
                    +--+-----------+-----------+---------+
                       |           |           |
                       v           v           v
                 +---------+ +-----------+ +----------+
                 | Workers | | Policies  | | Adapters |
                 | claude  | | approval  | | GitHub   |
                 | codex   | | command   | | Notion   |
                 | fake    | | cost      | | RDAP     |
                 +---------+ +-----------+ +----------+
                       |
                       v
        +------------------------------+
        | PostgreSQL (localhost:5442)  |
        | 19 tables: goals, tasks,     |
        | runs, approvals, audit, ...  |
        | also serves as work queue    |
        +------------------------------+
```

Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quickstart

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), Node 22, git, and the GitHub CLI (`gh`).

```bash
git clone <repo-url> nexus && cd nexus
make setup        # starts PostgreSQL, installs Python and Node deps, runs migrations
make dev          # control plane API on :8400 plus dashboard on :3400
```

Then:

- Dashboard: http://localhost:3400
- API: http://localhost:8400 (`GET /health`, `GET /api/system/status`)
- Environment check: `cd services/control-plane && uv run nexus doctor`

A full clean-machine walkthrough, including a first fake-worker goal, is in [docs/runbooks/LOCAL-SETUP.md](docs/runbooks/LOCAL-SETUP.md).

## CLI examples

Run from `services/control-plane`:

```bash
uv run nexus doctor                       # environment, tools, auth, DB, repo health
uv run nexus bootstrap                    # migrations plus baseline records
uv run nexus start --foreground           # API plus orchestrator (foreground)
uv run nexus status                       # control plane, cost mode, worker availability

uv run nexus goal create \
  -t "Prove the loop" \
  -d "End-to-end check [fake:write=proof.md]" \
  --worker fake                           # zero model usage

uv run nexus goal list
uv run nexus task list
uv run nexus run list
uv run nexus run inspect <run_id>         # event timeline for one run
uv run nexus run cancel <run_id>

uv run nexus worker list                  # health of fake, claude-code, codex-cli
uv run nexus worker test claude-code --live   # opt-in live smoke (uses subscription)

uv run nexus notion setup                 # store token in .env (chmod 600)
uv run nexus notion bootstrap             # idempotent Notion workspace creation
uv run nexus domain search --word nexus --tone authority --count 10
```

## What is implemented vs planned

| Area | Status |
| --- | --- |
| Domain model (19 tables), Alembic migration | Implemented |
| Explicit state machines for Goal, Task, Run | Implemented |
| PostgreSQL work queue (`SELECT ... FOR UPDATE SKIP LOCKED`) | Implemented |
| Orchestrator: dispatch, evidence-based validation, bounded repair, cancellation, audit trail | Implemented |
| Deterministic planner (no model usage) | Implemented |
| FakeWorker (deterministic, drives the full loop) | Implemented |
| Claude Code adapter (verified against installed CLI 2.1.x) | Implemented |
| Codex CLI adapter (fixture-tested; CLI not installed on this machine) | Implemented |
| Rules-based worker routing plus cross-review | Implemented |
| Approval policy engine, approvals API, and dashboard page | Implemented |
| Orchestrator creating approval gates mid-execution | Planned |
| Command execution policy (allowlists, redaction, workspace confinement) | Implemented |
| Cost policy (all paid services disabled) | Implemented |
| Git worktree isolation module | Implemented standalone; automatic wiring into repo-backed goals is scaffolded |
| Validation runner (files-exist, make test/lint/typecheck/build) | Implemented |
| Structured logging with secret redaction | Implemented |
| GitHub adapter (issues, draft PRs, labels, branch push via gh CLI) | Implemented |
| GitHub review-comment to follow-up-task translation | Planned |
| Notion adapter: workspace bootstrap (pages plus 4 databases) | Implemented |
| Notion goal/task record sync | Scaffolded (`nexus notion sync` exits with a clear message) |
| Live model-based planning | Planned |
| Background daemon mode | Planned (`nexus start` runs foreground today) |
| API authentication | Planned (localhost-bound, single-owner placeholder today) |
| Hybrid/remote deployment | Planned (design: secrets stay local) |
| Domain discovery skill (`nexus domain search`) | Implemented |

## Documentation

- [VISION.md](VISION.md) — what Nexus is for
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flow, data model
- [docs/OPERATING-MODEL.md](docs/OPERATING-MODEL.md) — execution principles and autonomy limits
- [docs/ROADMAP.md](docs/ROADMAP.md) — milestones with honest statuses
- [SECURITY.md](SECURITY.md) and [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md)
- [docs/COST-GUARDRAILS.md](docs/COST-GUARDRAILS.md) — binding cost policy
- [docs/WORKER-ROUTING.md](docs/WORKER-ROUTING.md) and [docs/AUTONOMY-AND-APPROVALS.md](docs/AUTONOMY-AND-APPROVALS.md)
- [docs/NOTION-INTEGRATION.md](docs/NOTION-INTEGRATION.md) and [docs/GITHUB-INTEGRATION.md](docs/GITHUB-INTEGRATION.md)
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and [CONTRIBUTING.md](CONTRIBUTING.md)
- Runbooks: [local setup](docs/runbooks/LOCAL-SETUP.md), [recovery](docs/runbooks/RECOVERY.md), [adding a worker](docs/runbooks/ADDING-A-WORKER.md)
- Decisions: [docs/adr/](docs/adr/)
- Agent instructions: [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md)
