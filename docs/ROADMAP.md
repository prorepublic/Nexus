# Roadmap

Statuses are honest: **done** means implemented and tested in this repo today;
**scaffolded** means supporting pieces exist but the feature does not work end
to end; **planned** means not started beyond design.

## Milestone 0 — Bootstrap foundation (done)

Monorepo, control plane (FastAPI/SQLAlchemy/Alembic/PostgreSQL), explicit state
machines, SKIP LOCKED queue, fake worker, Claude/Codex adapters, deterministic
planner, dashboard and CLI basics, CI, documentation, draft PR #1.

## Milestone 1 — Development Automation V1 (done)

| Item | Status |
| --- | --- |
| Centralized execution subsystem: ProcessRunner + purpose-specific profiles, path confinement, process-group termination, audit (ADR-006) | Done |
| Fail-closed validation with per-repository validation profiles and trust levels (ADR-007) | Done |
| Repository onboarding (`nexus repo add/inspect/doctor/trust/validation`) | Done |
| Git worktrees wired end to end with chained baselines; final branch becomes the PR head | Done |
| Live AI planning with schema validation, bounded repair, deterministic fallback (ADR-008) | Done |
| Independent cross-agent review with structured findings and contextual bounded repair (ADR-009) | Done |
| Approval gates enforced in the engine: approve-plan, run-untrusted-repository-scripts, single-worker-review-fallback; block and deterministic resume | Done |
| Queue reliability: leases, heartbeats, stale-run recovery, failure classification, rate-limit cooldown and reroute (ADR-010) | Done |
| GitHub delivery: safe push + one draft PR per goal with evidence; PR feedback import to repair tasks, PR updates, resolution replies (ADR-011) | Done |
| Notion sync engine: 10 databases, idempotent one-way DB to Notion sync (ADR-004); needs the owner's integration token to run live | Done (code + tests) |
| Service management: background start, stop/restart/logs/recover, launchd install (ADR-012) | Done |
| Local-owner API protection: Host allowlist + custom-header preflight forcing (ADR-013) | Done |
| Operational dashboard (repositories, plans, task detail, PRs, settings) and full CLI | Done |
| Codex CLI auto-install + live smoke verification through the worker-codex profile | Done |
| End-to-end proof on a disposable fixture repository (live planning, Codex implementation, real pytest validation, bounded repair, operator retry, draft PR, feedback repair) | Done (see [E2E-DEMONSTRATION.md](E2E-DEMONSTRATION.md)) |

## Open items (blocked on owner credentials)

| Item | Blocked on |
| --- | --- |
| Claude Code as live worker/planner/reviewer (adapter implemented and profile-enforced; standalone CLI credentials are stale) | Owner runs `claude` once and `/login` with the Claude Max account, then `nexus worker enable claude-code` and `uv run nexus worker test claude-code --live` |
| Live Notion workspace bootstrap and sync | Owner creates an internal integration, shares a parent page, runs `nexus notion setup` |

## Milestone 2 — Hardening (planned)

- Containerized validation for untrusted repositories (today: approval gate).
- OpenAPI-generated TypeScript contracts in `packages/contracts`.
- Review-thread-level (inline) PR comment intake with file/line anchors.
- Worktree retention policies and automatic cleanup of delivered goals.
- Cross-repository goals.

## Milestone 3 — Hybrid and remote (planned)

- Remote status surfaces (sensitive code and credentials stay local).
- Explicitly authorized remote runners.
- Local authentication placeholder replaced with Entra ID.

## Later modules (explicitly out of V1 scope)

Voice interaction, email/calendar management, Teams integration, personal
productivity, mobile applications, public SaaS. The architecture keeps these
possible; none of them may delay development automation.
