# Runbook: Local Setup

Step-by-step from a clean macOS machine to a working Nexus with a first goal executed.

## 1. Install prerequisites

```bash
# Docker Desktop (provides docker + docker compose)
# Download from https://www.docker.com/products/docker-desktop/ and start it once.

# uv (Python toolchain manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Node 22 (via Homebrew, or nvm if you prefer)
brew install node@22

# git and the GitHub CLI
brew install git gh
gh auth login        # authenticate the gh CLI (needed for GitHub features)
```

Optional but recommended: the Claude Code CLI (`npm install -g @anthropic-ai/claude-code`, then run `claude` once to log in) so the claude-code worker is available. The Codex CLI (`npm install -g @openai/codex`, then `codex login`) is optional; Nexus reports its absence honestly.

## 2. Clone and set up

```bash
git clone <repo-url> nexus
cd nexus
make setup
```

`make setup` starts PostgreSQL (Docker, 127.0.0.1:5442), installs Python deps with uv, installs dashboard deps with npm, and applies migrations. No `.env` is required for local defaults; copy `.env.example` to `.env` only when you want overrides.

## 3. Start and verify

```bash
make dev
```

- Dashboard: http://localhost:3400 (shows health, cost mode, workers)
- API: http://localhost:8400/health

In a second terminal:

```bash
cd services/control-plane
uv run nexus doctor
```

Expected: OK for git, github-cli, docker, node, npm, python, postgresql; `worker:fake` OK; `worker:claude-code` OK if installed (auth reported as unknown until a live run); `worker:codex-cli` WARN if not installed; notion WARN until configured (optional).

Seed baseline records once: `uv run nexus bootstrap`.

## 4. First goal (fake worker, zero model usage)

With `make dev` running:

```bash
cd services/control-plane
uv run nexus goal create \
  -t "Prove the loop" \
  -d "End-to-end orchestration check [fake:write=proof.md]" \
  --worker fake
```

Watch it complete:

```bash
uv run nexus goal list        # goal: ready -> executing -> review
uv run nexus task list        # implementation task + review task
uv run nexus run list
uv run nexus run inspect <run_id>    # event timeline incl. "wrote proof.md" and validation
```

The fake worker writes `proof.md` into the goal's scratch workspace under `~/.nexus/workspaces/scratch/<goal_id>/`, validation confirms it, and the goal settles to `review`. The same flow is visible in the dashboard. Directive reference: [../DEVELOPMENT.md](../DEVELOPMENT.md).

## Troubleshooting

**Port 5442 already in use.** Another process owns the port. Either stop it, or set `NEXUS_DB_PORT=5443` (or similar) in `.env` at the repo root and set `NEXUS_DATABASE_URL` accordingly, then `make db-up && make migrate`.

**npm installs or Next.js fetches hang / fail with network errors on macOS.** Some Macs need `NODE_OPTIONS=--no-network-family-autoselection`. The root Makefile exports it for make targets; export it in your shell for manual npm commands:

```bash
export NODE_OPTIONS=--no-network-family-autoselection
```

**`uv: command not found`.** The installer puts uv in `~/.local/bin`; ensure that is on PATH (`echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc`) and restart the shell. `nexus doctor` flags a missing uv as a warning.

**`postgresql unreachable` in doctor.** Docker Desktop is not running or the container is down: start Docker, then `make db-up`.

**`control plane unreachable` from `nexus status` or the dashboard.** The API is not running; start it with `make dev` or `make dev-api`. Note `nexus start` runs in the foreground (background daemonization is not implemented yet).

**Stuck or half-failed state.** See [RECOVERY.md](RECOVERY.md).
