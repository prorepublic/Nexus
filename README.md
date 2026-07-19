# Nexus

Nexus is a secure, local-first, provider-independent AI development orchestration platform. The owner submits a high-level development Goal; Nexus plans it into Tasks (live AI planning with a deterministic fallback), routes each Task to a Worker (Claude Code CLI, Codex CLI, or a deterministic fake worker), executes it in an isolated git worktree, validates the result fail-closed with evidence, has a different worker review the diff, repairs within bounded attempts using the actual findings, and delivers the outcome as one draft pull request per goal for human review.

**Status: Development Automation V1.** The full execution loop is implemented for real repositories: centralized subprocess execution behind purpose-specific profiles, repository onboarding and trust, fail-closed validation, live planning, independent cross-agent review with contextual repair, durable leases with crash recovery, GitHub delivery and feedback intake, one-way Notion sync, background service management, and a hardened localhost API. 216 tests pass (unit and integration; live worker tests are opt-in and never run in CI). What remains open is listed honestly in [docs/ROADMAP.md](docs/ROADMAP.md).

## Architecture

```
                        +---------------------------+
   Owner                |  Web Dashboard (Next.js)  |
   (goals, approvals)   |  localhost:3400           |
        |               +------------+--------------+
        |                            | HTTP (localhost, Host allowlist,
        v                            v  X-Nexus-Client on writes)
  +-----------+          +---------------------------+
  | nexus CLI |--------->|  Control Plane (FastAPI)  |
  +-----------+          |  localhost:8400           |
                         +------------+--------------+
                                      |
                    +-----------------+------------------+
                    |        Orchestrator loop           |
                    |  plan -> queue (leases) -> execute |
                    |  in worktree -> validate (fail-    |
                    |  closed) -> independent review ->  |
                    |  repair -> deliver draft PR        |
                    +--+-----------+-----------+---------+
                       |           |           |
                       v           v           v
                 +---------+ +-----------+ +----------+
                 | Workers | | Execution | | Adapters |
                 | claude  | | subsystem | | GitHub   |
                 | codex   | | 15 profi- | | Notion   |
                 | fake    | | les       | | RDAP     |
                 +---------+ +-----------+ +----------+
                       |
                       v
        +------------------------------+
        | PostgreSQL (localhost:5442)  |
        | 20 tables: goals, tasks,     |
        | runs, approvals, findings,   |
        | prompts, audit, ...          |
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

A full clean-machine walkthrough, including a first fake-worker goal and a first real repository goal, is in [docs/runbooks/LOCAL-SETUP.md](docs/runbooks/LOCAL-SETUP.md).

## CLI examples

Run from `services/control-plane`:

```bash
uv run nexus doctor                       # environment, tools, auth, DB, repo health
uv run nexus bootstrap                    # migrations plus baseline records
uv run nexus start                        # background by default; --foreground for dev
uv run nexus status                       # control plane, cost mode, worker availability
uv run nexus logs -n 200                  # tail the managed log file
uv run nexus stop | restart | recover     # lifecycle and crash recovery
uv run nexus install-service              # launchd: start at login, restart on crash

uv run nexus repo add ~/myrepo            # or owner/repo or a GitHub URL
uv run nexus repo trust myrepo trusted-local
uv run nexus repo validation set myrepo tests uv run pytest -q
uv run nexus repo doctor myrepo

uv run nexus goal create \
  -t "Add input validation" \
  -d "Validate the payload of POST /items" \
  --repo myrepo \
  -c "invalid payloads return 422" \
  --autonomy manual                       # plan requires approval before execution
uv run nexus goal inspect <goal_id>       # plan, tasks, workers, verdicts
uv run nexus goal approve-plan <goal_id>

uv run nexus task list | inspect | retry | cancel
uv run nexus run list | inspect | cancel
uv run nexus approval list                # pending gates (plan, untrusted scripts, ...)
uv run nexus approval approve <id>

uv run nexus worker list                  # health of fake, claude-code, codex-cli
uv run nexus worker doctor --install-codex
uv run nexus worker enable|disable codex-cli
uv run nexus worker route-explain implementation
uv run nexus worker test claude-code --live   # opt-in live smoke (uses subscription)

uv run nexus github status                # gh auth plus recent Nexus PRs
uv run nexus github feedback import <goal_id>  # PR comments -> repair tasks
uv run nexus notion setup | bootstrap | sync | doctor
uv run nexus domain search --word nexus --tone authority --count 10
```

## What is implemented vs planned

| Area | Status |
| --- | --- |
| Domain model (20 tables), Alembic migrations | Implemented |
| Explicit state machines for Goal, Task, Run | Implemented |
| Centralized execution subsystem: 15 purpose-specific profiles, path confinement, process-group kill, env filtering, audit ([ADR-006](docs/adr/ADR-006-centralized-execution-subsystem.md)) | Implemented (static test proves no subprocess use outside it) |
| PostgreSQL work queue with leases, heartbeats, stale-run recovery ([ADR-010](docs/adr/ADR-010-durable-worker-leases.md)) | Implemented |
| Repository onboarding, trust levels, per-repo validation profiles ([ADR-007](docs/adr/ADR-007-repository-trust-and-fail-closed-validation.md)) | Implemented |
| Fail-closed validation (blocked/skipped never pass; empty evidence never completes implementation) | Implemented |
| Live AI planning with schema validation, one repair attempt, deterministic fallback ([ADR-008](docs/adr/ADR-008-live-ai-planning.md)) | Implemented |
| Plan approval gate for manual-autonomy goals | Implemented and enforced |
| Independent cross-agent review with persisted findings and contextual repair ([ADR-009](docs/adr/ADR-009-independent-review-lifecycle.md)) | Implemented |
| Git worktrees wired end to end for repository-backed goals, chained baselines | Implemented |
| Approval gates created by the engine mid-execution, block/resume semantics | Implemented |
| GitHub delivery: safe push plus one draft PR per goal with evidence ([ADR-011](docs/adr/ADR-011-github-delivery-and-feedback.md)) | Implemented |
| GitHub PR feedback import (conservative, exactly-once, evidence-backed replies) | Implemented (owner-triggered) |
| Notion: idempotent bootstrap (10 databases) plus one-way DB-to-Notion sync | Implemented (needs the owner's integration token) |
| Service management: background start, stop/restart/logs/recover, launchd ([ADR-012](docs/adr/ADR-012-service-management-and-recovery.md)) | Implemented |
| Localhost API protection: Host allowlist, X-Nexus-Client, CORS ([ADR-013](docs/adr/ADR-013-local-owner-api-protection.md)) | Implemented |
| Worker enable/disable, routing explanation, usage tracking on runs | Implemented |
| Cost policy (all paid services disabled; no fabricated monetary cost) | Implemented |
| Containerized validation for untrusted repositories | Planned (approval gate is the current control) |
| Claude Code standalone login on this machine | Owner action needed (`claude` once, then `/login`) |
| API user authentication (Entra ID considered) | Planned |
| OpenAPI-generated TypeScript contracts | Planned |
| Hybrid/remote deployment | Planned (design: secrets stay local) |

## Documentation

- [VISION.md](VISION.md) — what Nexus is for
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flow, data model
- [docs/OPERATING-MODEL.md](docs/OPERATING-MODEL.md) — execution principles and autonomy limits
- [docs/ROADMAP.md](docs/ROADMAP.md) — milestones with honest statuses
- [SECURITY.md](SECURITY.md) and [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md)
- [docs/REPOSITORY-TRUST.md](docs/REPOSITORY-TRUST.md) and [docs/VALIDATION-PROFILES.md](docs/VALIDATION-PROFILES.md)
- [docs/COST-GUARDRAILS.md](docs/COST-GUARDRAILS.md) — binding cost policy
- [docs/WORKER-ROUTING.md](docs/WORKER-ROUTING.md) and [docs/AUTONOMY-AND-APPROVALS.md](docs/AUTONOMY-AND-APPROVALS.md)
- [docs/NOTION-INTEGRATION.md](docs/NOTION-INTEGRATION.md) and [docs/GITHUB-INTEGRATION.md](docs/GITHUB-INTEGRATION.md)
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and [CONTRIBUTING.md](CONTRIBUTING.md)
- Runbooks: [local setup](docs/runbooks/LOCAL-SETUP.md), [recovery](docs/runbooks/RECOVERY.md), [adding a worker](docs/runbooks/ADDING-A-WORKER.md)
- Decisions: [docs/adr/](docs/adr/)
- Agent instructions: [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md)
