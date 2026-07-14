# Contributing

Nexus is a single-owner project, but all work — human or coding agent — follows the same rules. Agent-specific instructions live in [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md); this file covers the development workflow.

## Development setup

Prerequisites: Docker Desktop, uv, Node 22, git, gh. Then:

```bash
make setup     # db-up + uv sync + npm install + migrations
make dev       # API (127.0.0.1:8400) + dashboard (localhost:3400)
```

Verify the environment with `cd services/control-plane && uv run nexus doctor`. Detailed steps and troubleshooting: [docs/runbooks/LOCAL-SETUP.md](docs/runbooks/LOCAL-SETUP.md).

## Make targets

| Target | Purpose |
| --- | --- |
| `make setup` | Install all dependencies, start PostgreSQL, apply migrations |
| `make doctor` | Environment health report |
| `make db-up` / `make db-down` | Start / stop PostgreSQL (Docker Compose, localhost:5442) |
| `make migrate` | `alembic upgrade head` |
| `make dev` / `make dev-api` / `make dev-web` | Run both / API only / dashboard only |
| `make test` | Unit tests (no DB, no network, no live workers) |
| `make test-integration` | Integration tests (requires PostgreSQL; runs `db-up` first) |
| `make lint` | ruff check + ruff format check + next lint |
| `make typecheck` | mypy (Python) + tsc --noEmit (TypeScript) |
| `make security` | pip-audit + npm audit (free, local) |
| `make build` | Production build of the dashboard |
| `make validate` | lint + typecheck + test + build — run before claiming any change done |
| `make clean` | Remove caches and build artifacts |

## Tests

Layout: `services/control-plane/tests/{unit,integration,live,fixtures}`.

Markers (defined in `services/control-plane/pyproject.toml`):

- **default (unit)** — `make test` runs `pytest -m "not integration and not live"`. No database, no network, no model usage. This is the default bar for every change.
- **`integration`** — requires a running PostgreSQL: `make db-up` then `make test-integration`.
- **`live`** — invokes a real worker CLI and consumes subscription usage. Strictly opt-in, never run in CI, never run automatically. Prefer `uv run nexus worker test <name> --live` for a controlled smoke test.

Behavior changes require tests. Worker adapters are tested against recorded fixtures in `tests/fixtures/` rather than live CLIs.

## Style

- Python: ruff (lint + format, line length 100) and mypy must both pass. Python 3.12, SQLAlchemy 2 style, type hints on public interfaces.
- TypeScript: strict mode; `npx tsc --noEmit` and `npm run lint` must pass.
- Follow the conventions in [CLAUDE.md](CLAUDE.md): state transitions only through `domain/transitions.py`, subprocesses only through `CommandExecutor` or the adapters, no direct paid API calls.

## Commits and branches

- Branch per change; never commit directly to `main`. Nexus-generated task branches follow `nexus/<goal8>/<task8>`.
- Commit messages: imperative subject line, under 72 characters, with a body explaining why when the change is not obvious. Reference the goal/task or issue where one exists.
- Never force-push shared branches. Never commit `.env`, tokens, or generated caches.
- Open PRs as drafts; merging to `main` is an owner decision.

## ADR process

Architectural decisions are recorded in [docs/adr/](docs/adr/) using the standard format (Status, Date, Context, Decision, Consequences), numbered sequentially (`ADR-006-...` is next).

- Read the existing ADRs before proposing a change that touches their subject matter (queueing, worker interfaces, Notion, autonomy model, deployment shape).
- To change a recorded decision, write a new ADR that supersedes the old one and mark the old one "superseded". Do not silently contradict an accepted ADR in code.
- Reversible choices do not need an ADR; hard-to-reverse ones do.
