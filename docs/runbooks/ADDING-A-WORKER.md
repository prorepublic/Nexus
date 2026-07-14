# Runbook: Adding a Worker

How to add a new Worker adapter (a new CLI tool or, later, an API-backed model) behind the provider-independent contract. Read [../adr/ADR-002-cli-worker-adapters.md](../adr/ADR-002-cli-worker-adapters.md) first; the contract exists precisely so this procedure never touches domain code.

## 1. Implement `WorkerAdapter`

Create `src/nexus/workers/<name>.py` implementing the ABC in `src/nexus/workers/base.py`:

- `health_check() -> WorkerHealth`
- `capabilities() -> WorkerCapabilities` (declare supported `TaskKind`s)
- `execute(spec, on_event) -> WorkerResult`
- `cancel(run_id) -> bool` (best-effort; track Popen handles like the existing adapters)
- optionally `prepare_workspace` and `estimate_suitability`

Requirements for `execute`:

- Non-interactive official interface only, argv list, `cwd` = `spec.workspace`, bounded by `spec.timeout_seconds` (kill on expiry, return `error_category="timeout"`).
- Honor `spec.read_only`: planning/review runs must not be able to write (read-only tool set, read-only sandbox, or equivalent). Never use permission-bypass flags (the equivalents of `--dangerously-skip-permissions` / `danger-full-access`).
- Translate provider output into neutral `WorkerEvent`s and one `WorkerResult`; classify failures into the shared `error_category` values (timeout | auth | limit | crash | policy | cli-flags | unknown) so exhaustion rerouting and triage work.
- Redact everything you persist (`redact_text` from `nexus.observability`) and cap output sizes, following `claude_code.py` / `codex_cli.py`.

## 2. health_check honesty rules

These are non-negotiable (see the existing adapters):

- Report `installed=False` with actionable install instructions when the binary is not on PATH.
- Report `authenticated=None` ("unknown") unless authentication can be verified **without spending model usage** (Codex has `codex login status`, so it can report True/False; Claude cannot cheaply, so it reports unknown until a live run).
- Never assume availability; `available` is derived as `installed and authenticated is not False`.
- Flag mismatches on the installed CLI must surface as structured `cli-flags` errors, never be silently adapted around.

## 3. Parser + fixture tests

- Record representative CLI output (success, failure, both event shapes if the CLI has versioned formats) into `tests/fixtures/<name>_*.jsonl`. Redact anything sensitive in the fixtures.
- Test the parser against fixtures in `tests/unit/test_worker_parsers.py`: result extraction, session/thread reference, event emission, error categorization. Use `iter_jsonl` for defensive JSONL parsing.
- Unit tests must not invoke the real CLI. `make test` must pass with the CLI absent.

## 4. Register the worker

1. `WorkerName` enum in `src/nexus/domain/enums.py` — add the new name.
2. `src/nexus/workers/registry.py` — instantiate the adapter in `WorkerRegistry`'s default map.
3. Routing preferences in `src/nexus/routing/router.py` (`DEFAULT_PREFERENCES`) — insert the worker where it belongs per task kind, and consider the cross-review implications ([../WORKER-ROUTING.md](../WORKER-ROUTING.md)).
4. Provider name maps: `provider_names` in `src/nexus/api/app.py` and the seed list in `nexus bootstrap` (`src/nexus/cli/main.py`), so status surfaces and the workers table name the provider correctly.
5. If the CLI binary should be configurable, add a `<name>_bin` setting in `src/nexus/config.py`; if validation may run it, consciously extend the command-policy allowlist.

## 5. Live smoke test pattern

Follow the existing opt-in pattern rather than inventing one:

- `uv run nexus worker test <name>` prints health only (free).
- `uv run nexus worker test <name> --live` runs a one-line read-only smoke task ("Reply with exactly: NEXUS-WORKER-OK") in a temp directory with a short timeout. This consumes subscription usage and is never run automatically.
- Optionally add a `live`-marked test in `tests/live/test_live_workers.py` (excluded from `make test` and CI by marker).

## 6. Done means

`make validate` clean, fixture tests passing without the CLI installed, honest health output verified in `nexus doctor` / `nexus worker list`, docs updated ([../WORKER-ROUTING.md](../WORKER-ROUTING.md) availability section and, if defaults changed, the preferences table).
