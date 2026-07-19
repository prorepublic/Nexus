# Development Guide

Practical workflows for working on the Nexus control plane and dashboard. Setup from scratch: [runbooks/LOCAL-SETUP.md](runbooks/LOCAL-SETUP.md). Rules: [../AGENTS.md](../AGENTS.md), [../CONTRIBUTING.md](../CONTRIBUTING.md).

## uv workflows

The control plane (`services/control-plane`) is managed with [uv](https://docs.astral.sh/uv/). Everything runs through `uv run`, which keeps `.venv` in sync with `pyproject.toml`/`uv.lock`:

```bash
cd services/control-plane
uv sync --all-extras            # install runtime + dev dependencies
uv run nexus doctor             # any console script
uv run pytest -m "not integration and not live" -q
uv run ruff check src tests && uv run ruff format src tests
uv run mypy src
uv add <package>                # add a dependency (updates pyproject + lock)
uv add --optional dev <package> # dev-only dependency
```

Note: this Mac needs `NODE_OPTIONS=--no-network-family-autoselection` for npm networking; the root Makefile exports it already.

## Alembic migration workflow

1. Edit the models in `src/nexus/db/models.py`.
2. Ensure Postgres is up (`make db-up`) and current: `uv run alembic upgrade head`.
3. Generate: `uv run alembic revision --autogenerate -m "add <thing>"`.
4. **Review the generated file** in `alembic/versions/` — autogenerate misses server defaults, enum changes, and data migrations. Prefer additive changes; destructive migrations are an approval-gated action.
5. Apply: `uv run alembic upgrade head` (or `make migrate` from the root).
6. Verify downgrade sanity if you wrote one; run `make test-integration`.

History and rollback: `uv run alembic history`, `uv run alembic downgrade -1`. Recovery from a broken migration: [runbooks/RECOVERY.md](runbooks/RECOVERY.md).

## Adding an API endpoint

1. Define request/response models in `src/nexus/api/schemas.py` (Pydantic).
2. Add the route in `src/nexus/api/app.py` following the existing pattern: `Depends(get_db)` for the session, `record_audit(...)` for consequential actions, statuses changed only via `domain/transitions.py`.
3. Keep responses free of secrets and internal paths; large text fields are truncated at write time (see existing `[:2000]`-style caps).
4. Add tests in `tests/integration/test_api.py` (marked `integration`; uses a real Postgres).
5. If the dashboard consumes it, mirror the types in `apps/web/src/lib/api.ts` (until `packages/contracts` generates them — currently a scaffold).

## Adding a validation kind

1. Add the enum member to `ValidationKind` in `src/nexus/domain/enums.py`.
2. Wire it in `src/nexus/services/validation.py`: either bespoke logic (like `FILES_EXIST`) or a command entry in `_COMMANDS` with its marker file. Commands run through `CommandExecutor` — if the executable is new, it must be added to the command policy allowlist consciously.
3. Unknown kinds currently pass with "no automated check defined; skipped" — keep that honest-skip behavior for kinds that cannot run in a given workspace.
4. Add unit tests; add the Notion/docs mention if it becomes part of the default validation plan.

## Test layout and markers

```
tests/unit/           default; no DB, no network, no model usage
tests/integration/    marked `integration`; requires make db-up
tests/live/           marked `live`; invokes real worker CLIs — opt-in only,
                      consumes subscription usage, never run in CI
tests/fixtures/       recorded worker CLI output (claude stream-json, codex JSONL)
```

- `make test` = `pytest -m "not integration and not live"`.
- `make test-integration` = `pytest -m integration` (starts the DB first).
- Adapter parser changes must be exercised against fixtures, not live CLIs.

## Fake worker directives

`FakeWorker` (`src/nexus/workers/fake.py`) drives the entire loop deterministically. Behavior is controlled by directives embedded anywhere in the task instruction (and therefore in a goal description, since the planner copies it in):

| Directive | Effect |
| --- | --- |
| `[fake:fail]` | Always fail |
| `[fake:fail-first=N]` | Fail the first N attempts, then succeed (exercises the repair loop) |
| `[fake:write=path]` | Write a marker file at `path` inside the workspace (pairs with files-exist validation; path escapes are refused as policy violations) |
| `[fake:sleep=S]` | Sleep S seconds (cancellation and timeout tests) |

Example end-to-end goal with zero model usage:

```bash
uv run nexus goal create -t "Repair loop demo" \
  -d "Prove bounded repair [fake:fail-first=1] [fake:write=proof.md]" \
  --worker fake
```

## Debugging with `nexus run inspect`

```bash
uv run nexus run list                 # find the run id
uv run nexus run inspect run_...      # status, attempt, summary, event timeline
```

The timeline shows worker events (started, tool, command, result, error) and validation events with pass/fail summaries. For deeper digging: `uv run nexus task list` for attempt counts, the `audit_events` table for the decision trail, and the dashboard run detail page. Cancel a stuck run with `uv run nexus run cancel <id>`; broader triage in [runbooks/RECOVERY.md](runbooks/RECOVERY.md).
