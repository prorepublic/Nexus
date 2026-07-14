# CLAUDE.md — Claude Code session guide for Nexus

Operational rules for Claude Code sessions in this repository. [AGENTS.md](AGENTS.md) is the authoritative rule set for all coding agents; this file adds the practical details. Read the ADRs in [docs/adr/](docs/adr/) before changing anything they cover.

## Build and test commands

All from the repo root:

```bash
make setup              # deps + Postgres + migrations (first time)
make db-up              # start PostgreSQL (localhost:5442)
make dev                # API :8400 + dashboard :3400
make test               # unit tests (default bar; no DB, no network)
make test-integration   # needs db-up
make lint               # ruff + next lint
make typecheck          # mypy + tsc --noEmit
make validate           # lint + typecheck + test + build — run before claiming done
```

Python commands run under uv from `services/control-plane`, e.g. `uv run pytest tests/unit/test_transitions.py -q`, `uv run nexus doctor`, `uv run alembic upgrade head`.

Never run `live`-marked tests or `nexus worker test --live` unless the owner explicitly asks — they consume subscription usage.

## Layout

```
apps/web/                     Next.js 15 dashboard (TS strict, Tailwind 4, port 3400)
services/control-plane/       Python 3.12, FastAPI, SQLAlchemy 2, Alembic, uv
  src/nexus/domain/           enums + hand-written state machines (transitions.py)
  src/nexus/services/         orchestrator, queue, planner, validation, workspace, events
  src/nexus/workers/          WorkerAdapter contract + fake / claude_code / codex_cli + registry
  src/nexus/policies/         approval, command (CommandExecutor), cost
  src/nexus/routing/          rules-based router + cross-review
  src/nexus/adapters/         github (gh CLI), notion (REST), rdap
  src/nexus/api/              FastAPI app (localhost:8400)
  src/nexus/cli/              typer CLI (`uv run nexus ...`)
  tests/{unit,integration,live,fixtures}
packages/contracts/           scaffold only (future OpenAPI-generated TS types)
docs/                         architecture, roadmap, runbooks, adr/
```

## Key conventions

- **State transitions only through `domain/transitions.py`.** Illegal transitions must raise `InvalidTransition`; never assign statuses directly in orchestration code.
- **All subprocess execution via `CommandExecutor`** (`policies/command.py`) or the existing adapters with injectable runners. argv lists only, no shell strings.
- **No direct paid API calls.** Workers run through subscription CLIs behind the `WorkerAdapter` contract. Domain code never imports provider specifics.
- **Tests required for behavior changes.** Adapter parser changes need fixtures in `tests/fixtures/`. Do not weaken existing tests to get green.
- **Secrets:** never committed, never logged, never in prompts. Redaction lives in `observability.py`; extend it if you add a new secret shape.
- **Honesty in status reporting:** health checks and docs must state what is actually verified (e.g. Claude auth is "unknown" until a live run). Never present scaffolded or planned behavior as implemented.
- New migration: edit `db/models.py`, then `uv run alembic revision --autogenerate -m "..."`, review the generated file, `uv run alembic upgrade head`. See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Definition of Done

Same as [AGENTS.md](AGENTS.md): acceptance criteria demonstrated, `make validate` clean, tests added, no policy violations, docs updated, work left on a branch for owner review. Merging to `main` and other gated actions in [SECURITY.md](SECURITY.md) are owner decisions.
