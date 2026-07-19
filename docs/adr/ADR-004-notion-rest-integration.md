# ADR-004: Notion via the official REST API with an internal integration

- Status: accepted
- Date: 2026-07-14

## Context

Nexus mirrors its planning and knowledge surface (vision, roadmap, backlog, decisions, agent catalog, and eventually goal/task records) into Notion, where the owner already organizes work. The owner is on the **Notion Free plan**, which rules out any approach that assumes premium features or paid connector infrastructure — including hosted premium MCP access. The cost policy ([../COST-GUARDRAILS.md](../COST-GUARDRAILS.md)) prohibits introducing paid services for this.

## Decision

Use the official Notion REST API v1 directly (`src/nexus/adapters/notion.py`) with an **internal integration token**, which is available on the Free plan. The token is captured by `nexus notion setup` into the local `.env` (chmod 600) and never logged, committed, stored in the database, or passed to prompts.

Workspace creation is an idempotent bootstrap (`nexus notion bootstrap`): pages and databases are looked up by title under the parent page before anything is created, and every created object is recorded as an `external_syncs` upsert, so reruns reconcile instead of duplicating. The client handles 429s via `Retry-After` and 5xx via bounded exponential backoff.

## Consequences

- Works on the Free plan with zero recurring cost and no third-party connector in the trust path; one bearer token is the entire credential surface, and it is covered by the redaction patterns.
- Idempotency makes the bootstrap safe to run repeatedly and safe to re-run after partial failures.
- The adapter is tested against an in-memory fake Notion, so the suite needs no network and no real workspace.
- Structured record sync (goals/tasks into the databases) rides on the same client and `external_syncs` plumbing but is deliberately deferred; `nexus notion sync` is an honest stub today ([../ROADMAP.md](../ROADMAP.md), Milestone 2).
- Title-based lookup means renaming a bootstrapped page in Notion causes the next bootstrap to create a fresh one; acceptable, documented in [../NOTION-INTEGRATION.md](../NOTION-INTEGRATION.md).
