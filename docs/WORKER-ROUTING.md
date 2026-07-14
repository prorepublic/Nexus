# Worker Routing

How Nexus decides which Worker executes a Task and which Worker reviews it. Source: `src/nexus/routing/router.py` (implemented and unit-tested).

## Default preferences

Rules-based defaults per task kind, first available worker in the list wins:

| Task kind | 1st preference | 2nd preference |
| --- | --- | --- |
| planning | claude-code | codex-cli |
| architecture | claude-code | codex-cli |
| review | claude-code | codex-cli |
| security-review | claude-code | codex-cli |
| documentation | claude-code | codex-cli |
| implementation | codex-cli | claude-code |
| testing | codex-cli | claude-code |
| refactoring | codex-cli | claude-code |
| maintenance | codex-cli | claude-code |

Rationale: Claude Code is preferred where cross-file reasoning and judgment dominate (planning, architecture, review, security, documentation synthesis); Codex CLI where the work is implementation-shaped (code, tests, refactoring, repetitive changes). If no preferred worker is available, the router falls back to any available worker; if none, it raises `NoWorkerAvailable` and the task fails with an audited `task.no-worker` event.

The fake worker is never a default preference; it runs only when explicitly requested (`--worker fake`), which is how the loop is exercised with zero model usage.

## Cross-review matrix

The reviewer must differ from the implementer whenever another worker is available, and the fake worker never reviews real work:

| Implementer | Reviewer |
| --- | --- |
| claude-code | codex-cli (when available) |
| codex-cli | claude-code (when available) |
| fake | fake, or none |
| any, single-worker setup | none — recorded honestly, not faked |

When multiple reviewer candidates exist, Claude is preferred for review when it is not the implementer. The reviewer is stored on the task (`task.reviewer`) at routing time. Review tasks always run read-only ([OPERATING-MODEL.md](OPERATING-MODEL.md)).

## Override mechanism

The owner can pin a worker per goal: `requested_worker` on the goal (CLI `--worker claude-code|codex-cli|fake`, or the worker selector in the dashboard's goal form, which also offers automatic routing). An override routes every task of that goal to the requested worker; if that worker is unavailable, routing fails loudly rather than silently substituting. Once a task has been routed, repair attempts reuse the same worker.

## Suitability scoring

`WorkerAdapter.estimate_suitability(kind)` returns 0.0 to 1.0; the default implementation returns 1.0 when the kind is in the adapter's declared capabilities and 0.2 otherwise. Today's router selects on the preference table plus availability; the suitability score exists on the contract so future routing can rank adapters more finely without changing domain code.

## Subscription-exhaustion rerouting

When a worker hits its subscription limit (adapter reports `error_category="limit"`; tracked by `WorkerBudgetState`), it becomes unavailable to the router. Queued tasks reroute to another capable worker when the rules allow it safely; otherwise they wait in the queue. Nexus never falls back to paid APIs. Details: [COST-GUARDRAILS.md](COST-GUARDRAILS.md).

## Worker availability today

- **fake** — always available (in-process, deterministic).
- **claude-code** — installed and verified against claude 2.1.x; authentication reported as "unknown" until a live run confirms it (`nexus worker test claude-code --live`, opt-in).
- **codex-cli** — adapter implemented against the documented interface and fixture-tested, but the CLI is not installed on this machine; the health check reports that honestly.
