# ADR-003: PostgreSQL work queue instead of Redis or a message broker

- Status: accepted
- Date: 2026-07-14

## Context

Tasks must be dispatched to workers safely: no double-claiming when multiple orchestrator instances run, dependency gating before enqueue, and a durable record of what happened. The conventional answer is Redis or a message broker, but Nexus already requires PostgreSQL for its domain state, the expected throughput is tiny (single-owner development workloads, seconds-to-minutes per task), and every extra stateful service adds operational burden and potential cost to a local-first, cost-constrained system.

## Decision

The work queue is PostgreSQL itself (`src/nexus/services/queue.py`). Claiming uses `SELECT ... FOR UPDATE SKIP LOCKED` with the status flip to `running` in the same transaction, so concurrent orchestrators cannot double-claim. Dependency satisfaction gates enqueue; dependents of terminally failed or cancelled tasks are cancelled so goals settle honestly. No Redis, no broker.

## Consequences

- One stateful dependency for everything: state, queue, events, and audit live in the same database, so a claim, its status transition, and its audit row commit atomically (audit locality).
- Zero additional infrastructure, cost, or failure modes; backup/reset procedures cover the queue for free.
- Throughput ceiling is far below a real broker's — irrelevant at this scale, and the polling loop (1 s default) adds negligible latency.
- Concurrency model is simple: run more orchestrator instances against the same table if needed.

## Revisit criteria

Reconsider (via a superseding ADR) if any of these become true: sustained task throughput beyond what row-lock polling handles comfortably (hundreds of claims/second), a need for cross-machine work distribution where a shared Postgres is unavailable, pub/sub fan-out requirements that outgrow `run_events` polling, or queue latency requirements below the polling interval.
