# Validation Profiles

What Nexus executes to verify task output, where those commands come from, and the fail-closed rules that decide completion. Design decision: [adr/ADR-007-repository-trust-and-fail-closed-validation.md](adr/ADR-007-repository-trust-and-fail-closed-validation.md). Source: `src/nexus/services/validation.py`.

## Principle

Validation commands come from a per-repository profile **stored in Nexus** (PostgreSQL, `repositories.validation_profile`), never implicitly from repository content. A Makefile in the repository is repository-controlled code; the profile is owner-controlled configuration. A PR that edits the Makefile cannot change what Nexus executes.

## Schema

The profile maps validation kinds to argv commands:

```json
{
  "lint":      { "command": ["uv", "run", "ruff", "check", "."] },
  "typecheck": { "command": ["uv", "run", "mypy", "."] },
  "tests":     { "command": ["uv", "run", "pytest", "-q"] }
}
```

Rules, enforced by `validate_profile_config` **before anything persists or executes**:

- keys must be known `ValidationKind` values: `tests`, `integration-tests`, `lint`, `format`, `typecheck`, `build`, `security`, `secret-scan`, `dependency-audit`, `migration-check`, `files-exist`, `changed-scope`, `acceptance`;
- each entry is an object with a `command` that is a non-empty list of strings (argv — no shell strings, no interpolation);
- every command must already satisfy the `validation-trusted` execution profile (allowed executables: `uv`, `npm`, `npx`, `node`, `python3`, `pytest`, `ruff`, `mypy`, `make`, `tsc`, `alembic`, `go`, `cargo`), so a malicious profile cannot smuggle in an unlisted executable.

## Managing profiles

```bash
nexus repo validation show <name>
nexus repo validation set <name> tests uv run pytest -q
nexus repo validation set <name> lint npm run lint
```

`nexus repo add` suggests a profile from detected toolchains (pyproject.toml, package.json, go.mod, Cargo.toml); suggestions are stored but still require raised trust before they execute ([REPOSITORY-TRUST.md](REPOSITORY-TRUST.md)). `nexus repo doctor` flags an invalid stored profile.

## Execution

Command-backed checks run inside the task's worktree through the `validation-trusted` execution profile: workspace-confined cwd, filtered environment, 900 s timeout, 1 MB output cap, results redacted and persisted per run (`validation_results`: kind, status, command, profile, exit code, duration, output tail).

Three built-in checks execute no repository code, at any trust level:

| Check | What it does |
| --- | --- |
| `files-exist` | Every expected file exists in the workspace; paths are confined (escapes are `error`); requesting it with no declared files is `blocked` |
| `changed-scope` | Changed files must match the task's declared `scope_globs`; skipped when no scope was declared |
| `secret-scan` | Pattern scan of changed files for obvious secret material (token prefixes, private key headers) |

## Fail-closed rules

`ValidationStatus` is five-valued, and only `passed` ever counts:

| Situation | Status | Passes? |
| --- | --- | --- |
| Command ran, exit 0 | `passed` | Yes |
| Command ran, non-zero exit | `failed` | No |
| Check not requested for this task | `skipped` | Never |
| Requested check with no configured command | `blocked` | Never |
| Trust level does not permit host execution | `blocked` (approval gate `run-untrusted-repository-scripts`) | Never |
| Command could not execute / timed out / path escape | `error` | Never |

Completion rules (`evidence_sufficient`):

- every requested outcome must be `passed` — one blocked or errored check fails the set;
- an **empty validation set never completes** an implementation, refactoring, or testing task;
- for an implementation task in a registered repository, `files-exist` and `changed-scope` alone are **insufficient** — at least one command-backed or content-inspecting check must pass;
- an insufficient or failing set triggers the bounded repair loop with the failure summaries in the repair context, then terminal failure.

## Where the checks come from per task

The planner assigns `validations` per task (ADR-008), constrained to known kinds; tasks with declared `expected_files` and no other validations get `files-exist`. The repository's profile supplies the commands for whatever kinds the task requests; a requested kind the profile does not define is `blocked`, which is the prompt to run `nexus repo validation set`.
