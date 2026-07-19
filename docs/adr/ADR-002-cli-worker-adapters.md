# ADR-002: Subscription CLIs behind a provider-independent worker contract

- Status: accepted
- Date: 2026-07-14

## Context

The owner already pays for Claude Max and ChatGPT Plus. Both subscriptions expose official, non-interactive CLIs (Claude Code `claude -p`, Codex `codex exec`) that can execute development tasks. Metered model APIs are prohibited by the cost policy ([../COST-GUARDRAILS.md](../COST-GUARDRAILS.md)). Browser automation of the chat products or scraping session tokens would violate terms of service and the project's security rules. Meanwhile, the orchestration domain must not be coupled to any single vendor's output format, flags, or failure modes — providers will change, and API-backed adapters may be added later.

## Decision

Workers are the official subscription CLIs, invoked through their documented non-interactive interfaces with explicit safety flags (read-only tool sets / sandboxes for planning and review, workspace-limited access, bounded turns, never permission-bypass flags).

Every worker sits behind the provider-independent contract in `src/nexus/workers/base.py`: `health_check`, `capabilities`, `estimate_suitability`, `prepare_workspace`, `execute`, `cancel`, exchanging neutral `TaskSpec` / `WorkerResult` / `WorkerEvent` structures. Domain logic never sees provider specifics; adapters translate at the edge. A deterministic FakeWorker implements the same contract so the entire loop is testable with zero model usage.

## Consequences

- No marginal cost per task; usage is bounded by the subscriptions, and exhaustion is handled by rerouting or queueing, never by paid fallback.
- API-backed model adapters can be added later without any domain changes — they are just another `WorkerAdapter` (still gated by the cost policy).
- CLI interfaces are a moving target: adapters parse defensively (both Codex event shapes are handled), flag mismatches surface as structured `cli-flags` errors, and parsers are pinned by fixture tests. Verified against installed claude 2.1.x; the Codex adapter follows documented behavior and reports honestly that the CLI is not installed on this machine.
- Health reporting must be honest by contract: authentication state is "unknown" when it cannot be verified without spending usage.
- Procedure for new workers: [../runbooks/ADDING-A-WORKER.md](../runbooks/ADDING-A-WORKER.md).
