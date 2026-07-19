# ADR-010: Durable leases, failure classification, and queue reliability

- Status: accepted
- Date: 2026-07-15

## Context

The PostgreSQL queue (ADR-003) made claiming safe, but a claimed task whose orchestrator process died stayed `running` forever, and the original engine held database sessions across worker execution — a model run can take half an hour, which is poison for connection pools and locks. Failures were also undifferentiated: an authentication error retried exactly like a flaky test, burning subscription usage on retries that could not possibly succeed.

## Decision

Queue reliability is built from leases, heartbeats, and failure classification (`src/nexus/services/queue.py`, `orchestrator.py`, `domain/enums.py`):

- **Leases**: every run records `lease_expires_at` (`lease_seconds = 2700`, task timeout plus validation headroom) and an `orchestrator_id`. A background heartbeat thread renews the lease every `heartbeat_seconds = 30` while the worker executes.
- **Stale-run recovery**: `recover_stale_runs` (each orchestrator tick, and `nexus recover`) fails any `running` run whose lease expired, classifies it as `infrastructure`, and requeues the task within its repair budget. Recovery locks rows with `FOR UPDATE SKIP LOCKED`, and claiming remains transactional, so **a task never executes twice concurrently**.
- **Transactions never span model execution**: task processing is phased — a short transaction prepares (claim, route, workspace, run row, instruction), the worker executes with no session open, then a fresh transaction records the outcome and runs validation/review.
- **Failure classification**: every failure maps to a `FailureCategory` (worker-crash, auth, rate-limit, timeout, invalid-output, policy-violation, validation-failed, review-rejected, merge-conflict, git-failure, tool-unavailable, infrastructure, owner-denied, cancelled, ...). Only categories in `RETRYABLE_FAILURES` re-queue; deterministic failures (auth, policy, tool-unavailable, owner denial) fail terminally and never consume model usage on pointless retries.
- **Rate limits are not failures of the task**: a rate-limited worker enters a cooldown (`worker_cooldown_seconds = 900`), the task's worker pin is cleared so it can reroute, and the attempt does **not** consume the repair budget.
- **Plan-approval gating** lives in the queue: a manual-autonomy goal's tasks are not enqueued until the plan is approved (ADR-008). Dependents of terminally failed or cancelled tasks are cancelled so goals settle honestly.
- **Idempotency and cycle safety**: tasks carry idempotency keys derived from plan, index, and title; dependency cycles are rejected at plan time (ADR-008), so the queue never sees an unsatisfiable graph.

## Consequences

- A crashed or killed orchestrator (power loss, `kill -9`, laptop sleep past the lease) is recovered automatically on the next tick or `nexus recover`; no manual database surgery.
- Retries are spent only where they can help; the audit trail records category, attempt, and budget for every decision, so "why did this retry / why did this not retry" is always answerable.
- Rate-limit handling matches subscription reality: back off the exhausted worker, reroute the work, keep the budget intact — never fall back to paid APIs.
- Long model runs no longer hold connections or locks; multiple orchestrators can share the queue safely.
- The lease window bounds how stale an orphaned run can be: at most `lease_seconds` after the last heartbeat.
