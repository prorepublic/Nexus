# Runbook: Recovery

Triage and recovery procedures for a misbehaving local Nexus. Commands run from `services/control-plane` unless noted.

## Stuck task or run

1. **Identify.** `uv run nexus run list` — look for long-lived `running` runs. `uv run nexus task list` shows task status and attempt counts.
2. **Inspect.** `uv run nexus run inspect <run_id>` prints status, attempt, exit summary, and the full event timeline (worker events, validation results). Most stalls are visible here: a worker waiting on a slow command, a timeout in progress, or repeated repair attempts.
3. **Check the audit trail.** The `audit_events` table records every consequential decision (`run.started`, `task.repair-scheduled`, `task.failed`, `goal.failed`, `run.cancel-requested`, ...):
   ```bash
   docker compose exec postgres psql -U nexus -d nexus \
     -c "SELECT ts, action, goal_id, task_id, run_id, status FROM audit_events ORDER BY ts DESC LIMIT 30;"
   ```
4. **Cancel if needed.** `uv run nexus run cancel <run_id>` (or the dashboard/API). Queued runs cancel immediately; running runs get a best-effort adapter cancellation (process terminate). Cancelling a goal (`POST /api/goals/{id}/cancel`) also cancels its non-started tasks.

Remember the built-in bounds: tasks time out at 1800 s and fail terminally after 3 attempts, so many "stuck" situations resolve themselves within those limits.

## Orchestrator restart

The orchestrator runs as a thread inside `nexus start` (foreground). To restart: Ctrl+C the `make dev` / `nexus start` process (or `uv run nexus stop`, which SIGTERMs the pid recorded in `~/.nexus/control-plane.pid`), then start it again. State is safe: the queue is PostgreSQL rows, claims are transactional, and an interrupted run simply shows as unfinished — cancel it and let the task retry within its budget if appropriate.

## Database reset (destructive)

**Warning: this deletes all goals, tasks, runs, approvals, and audit history.** There is no undo. Only for a local dev instance you are willing to wipe.

```bash
cd <repo-root>
docker compose down -v      # -v removes the nexus_pgdata volume
make setup                  # recreates the DB, reapplies migrations
cd services/control-plane && uv run nexus bootstrap
```

## Worktree cleanup

Nexus worktrees live under `~/.nexus/workspaces` (scratch dirs under `~/.nexus/workspaces/scratch/`). Nexus refuses to remove a worktree with uncommitted changes, so orphans can accumulate after crashes.

```bash
git worktree list                                  # run in the target repository
git worktree remove ~/.nexus/workspaces/<dir>      # refuses if dirty
git worktree remove --force ~/.nexus/workspaces/<dir>   # only after salvaging the diff
git worktree prune                                 # clear stale bookkeeping
git branch --list 'nexus/*'                        # leftover task branches
```

Before force-removing, capture anything valuable: `git -C ~/.nexus/workspaces/<dir> diff` and save the output. Do not delete branches that contain unique work — that is an approval-gated action for good reason. Scratch directories (fake-worker goals) are plain directories and can be deleted freely.

## Failed migration recovery

1. See where you are: `uv run alembic current` and `uv run alembic history --verbose`.
2. If an upgrade failed partway, fix the migration file, then step back and reapply:
   ```bash
   uv run alembic downgrade -1
   uv run alembic upgrade head
   ```
3. If the schema is inconsistent with Alembic's version table (e.g. a partially applied non-transactional change), prefer a full local reset (section above) over hand-editing — this is a dev database.
4. Destructive migrations against anything but a disposable local DB are approval-gated ([../AUTONOMY-AND-APPROVALS.md](../AUTONOMY-AND-APPROVALS.md)).
5. Validate afterwards: `make migrate && make test-integration`.
