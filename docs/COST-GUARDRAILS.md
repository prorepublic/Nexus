# Cost Guardrails

This policy is **binding** for all work in this repository, human or agent. It is enforced in code by `src/nexus/policies/cost.py` and by the approval gates `enable-paid-service` and `change-billing`.

## What the owner pays for

Exactly two subscriptions: **Claude Max** and **ChatGPT Plus**. Nexus uses them through their official CLIs (Claude Code, Codex CLI). Nothing else costs money.

## Default policy block

`DEFAULT_COST_POLICY` ships with every flag off and is surfaced verbatim through `GET /api/system/status` (`cost_mode`) and the dashboard banner:

| Flag | Default |
| --- | --- |
| `paid_model_apis` | false |
| `paid_hosting` | false |
| `paid_databases` | false |
| `paid_queues` | false |
| `paid_monitoring` | false |
| `paid_domain_services` | false |
| `automatic_purchasing` | false |

Prohibited without an explicit, owner-approved `enable-paid-service` / `change-billing` gate: metered model APIs, paid hosting, paid databases, paid queues or brokers, paid monitoring or logging services, paid domain services, and any automatic purchasing. Never silently enable billing — not "temporarily", not "for testing".

## Priority order when choosing tools

1. Existing subscriptions via official CLIs (Claude Code, Codex CLI).
2. Open source.
3. Local Docker.
4. Free tiers (e.g. Notion Free plan — see [NOTION-INTEGRATION.md](NOTION-INTEGRATION.md)).
5. Paid — only with explicit owner approval, recorded as an approval gate.

This ordering explains existing choices: PostgreSQL in local Docker instead of a hosted queue ([ADR-003](adr/ADR-003-postgresql-work-queue.md)), Notion REST internal integration instead of premium connectors ([ADR-004](adr/ADR-004-notion-rest-integration.md)), RDAP for domain lookups instead of a paid availability API.

## Subscription exhaustion behavior

Subscriptions have usage limits, not per-call prices. `WorkerBudgetState` (`src/nexus/policies/cost.py`) tracks exhaustion per worker; adapters classify rate/usage errors as `error_category="limit"`. When a worker's subscription is exhausted:

1. Mark the worker unavailable (it drops out of `Router.available`).
2. Reroute queued tasks to another capable worker when routing rules allow it safely.
3. Otherwise leave tasks queued until the limit window resets.
4. Surface the state via `nexus status`, `GET /api/system/status`, and the dashboard.
5. **Never** fall back to a paid API and never purchase additional capacity.

## Honesty about money

Nexus never claims monetary figures it cannot determine. Subscription usage does not map to dollars per task, so Nexus reports availability and exhaustion states, not invented costs. Live worker invocations that consume subscription usage (`nexus worker test --live`, `live`-marked tests) are strictly opt-in and never run automatically.
