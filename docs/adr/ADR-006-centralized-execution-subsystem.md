# ADR-006: One centralized execution subsystem with purpose-specific profiles

- Status: accepted
- Date: 2026-07-15

## Context

The bootstrap phase policed subprocesses with a single `CommandExecutor` allowlist (`policies/command.py`). One flat allowlist cannot express what V1 actually needs: a health check should never be able to push a branch, a git worktree operation should never be able to run `npm install`, and validation of an untrusted repository should not be able to run anything at all. Different call sites also need different timeouts, output budgets, environment surfaces, and network expectations. Meanwhile, the number of places that launch subprocesses grew (workers, git, gh, validation, migrations, tool install, launchd), and each uncoordinated call site is a potential policy bypass.

## Decision

All subprocess execution flows through a single subsystem, `src/nexus/execution/`:

- **`ProcessRunner`** (`runner.py`) is the only place `subprocess.Popen` is called for task-shaped work. It enforces the profile, confines the working directory, filters the environment to the profile's explicit passthrough list, streams stdout line-by-line (JSONL-friendly), caps output, redacts captured text, terminates the whole process group on timeout or cancellation (`start_new_session=True`, SIGTERM then SIGKILL), and records a `CommandExecution` audit row per invocation.
- **Fifteen purpose-specific profiles** (`profiles.py`), each narrowly defining permitted executables, permitted subcommands or two-token verbs, prohibited tokens, environment passthrough, timeout, output budget, write intent, documented network expectation, and an optional approval gate: `health-readonly`, `worker-claude`, `worker-codex`, `git-clone`, `git-readonly`, `git-worktree`, `git-commit`, `git-push`, `github-readonly`, `github-write-safe`, `validation-trusted`, `validation-untrusted`, `migration-local`, `service-local`, `tool-install`.
- **Prohibited tokens are refused in any argv position**, including `--flag=value` forms: force variants (`--force`, `-f`, `--force-with-lease`, `--hard`, `--mirror`, `--no-verify`, ...), worker permission bypasses (`--dangerously-skip-permissions`, `danger-full-access`, `--yolo`), and gh escalation (`--admin`, `--approve`, `--merge`, `--squash`, `--rebase`, `--delete-branch`). `gh pr merge` is additionally impossible because the verb pair is absent from both gh profiles.
- **Path confinement** (`paths.py`): every cwd and model-generated path is resolved against a permitted root; `..` traversal, absolute-path escape, and symlink escape are all rejected (candidates are fully resolved before comparison).
- **`validation-untrusted`** permits no executables at all; running repository-defined scripts from an untrusted repository requires the `run-untrusted-repository-scripts` approval gate (ADR-007).
- The one deliberate exception is `spawn_detached`, which starts Nexus's own control plane in the background; it hard-codes the module allowlist (`nexus.cli.main` only) and still uses its own process group so `nexus stop` can terminate the tree.

The old `CommandExecutor` (`policies/command.py`) is removed. A static unit test (`tests/unit/test_execution.py::test_no_direct_subprocess_outside_execution_subsystem`) scans the source tree and fails if direct `subprocess` use appears anywhere outside the runner (the GitHub adapter imports `subprocess` only for the `CompletedProcess` type; its production runner routes through the subsystem).

## Consequences

- There is exactly one enforcement point to audit and test; a new call site cannot quietly opt out because the static test catches direct subprocess use.
- Policy is expressed where it belongs: per purpose, not per binary. A health check and a worker run of the same binary get different rights.
- Every execution is audited (`command_executions`: redacted argv, cwd, exit code, duration, truncation flag, profile decision) and attributable to a run when one exists.
- Network policy is documented per profile but **not kernel-enforced** on macOS without extra tooling; this residual risk is stated in [../THREAT-MODEL.md](../THREAT-MODEL.md).
- Adding a new class of subprocess requires consciously defining a profile (executables, flags, budgets, gate), which is the intended friction.
