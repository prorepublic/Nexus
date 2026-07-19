# Repository Trust

How Nexus decides whether repository-defined code may execute on the host. Design decision: [adr/ADR-007-repository-trust-and-fail-closed-validation.md](adr/ADR-007-repository-trust-and-fail-closed-validation.md). Source: `src/nexus/services/repositories.py`, `src/nexus/services/validation.py`, `src/nexus/domain/enums.py` (`TrustLevel`, `HOST_EXECUTION_TRUST`).

## Why trust exists

Registering or cloning a repository must not grant it anything. A repository's Makefile, npm scripts, and test suite are code controlled by whoever wrote the repository; running them on the host is host execution. Nexus therefore separates two decisions: *working on* a repository (worktrees, diffs, built-in checks — always allowed once registered) and *executing repository-defined commands on the host* (allowed only at raised trust or behind an approval gate).

## Trust levels

Every registered repository has exactly one trust level (`repositories.trust_level`), starting at `untrusted`:

| Level | Meaning | Host execution of repo scripts |
| --- | --- | --- |
| `untrusted` (default) | Nothing about the content has been vouched for | No — blocked, approval gate offered |
| `reviewed` | The owner has read the repository but does not vouch for ongoing changes | No — blocked, approval gate offered |
| `trusted-local` | The owner's own local repository; content is the owner's responsibility | Yes |
| `trusted-owner-approved` | The owner explicitly approved host execution for this repository | Yes |

Only `trusted-local` and `trusted-owner-approved` are in `HOST_EXECUTION_TRUST`.

## What every trust level permits

Independent of trust level, a registered repository gets:

- git inspection and worktree lifecycle through the read-only and worktree execution profiles (never the repository's own tooling);
- isolated worktrees for tasks, diff capture, commits on task branches, safe pushes;
- the built-in validation checks that execute no repository code: `files-exist`, `changed-scope`, and `secret-scan`.

What raised trust adds is exactly one thing: the configured validation-profile commands (tests, lint, typecheck, build, ...) may run on the host, inside the task worktree, through the `validation-trusted` execution profile.

## How to raise trust

```bash
nexus repo trust <name> trusted-local          # or: reviewed | trusted-owner-approved
```

Also available from the dashboard repositories page and `POST /api/repositories/{id}/trust`. Every change writes a `repository.trust-changed` audit event. Lowering trust works the same way and takes effect on the next validation.

Raise trust deliberately:

- `trusted-local` is appropriate for repositories the owner authored and controls locally.
- `reviewed` is a statement of familiarity, not an execution grant; it exists so the owner can distinguish "looked at it" from "never looked at it".
- Third-party repositories should stay `untrusted` and use the approval-gated path below.

## The approval-gated path (untrusted and reviewed)

When a task in a low-trust repository reaches validation and a command-backed check is requested:

1. The check comes back `blocked` (fail-closed: blocked never passes).
2. The engine creates a **`run-untrusted-repository-scripts`** approval (risk: high) describing exactly which commands want to run, and moves the task to `blocked`.
3. `nexus approval list` / the dashboard approvals page show the gate. On **approve** (`nexus approval approve <id>`), the task resumes **exactly at the validation stage** (a `resume_stage` marker; the resumption does not consume an extra worker attempt) and the commands run as if the repository were owner-approved for this task.
4. On **deny**, the task is cancelled and the same gate is never re-asked for that task.

The approval is per task, not a standing grant; the repository's trust level is unchanged.

## Onboarding

Repo-backed goals require registration first — the API rejects unregistered repository names:

```bash
nexus repo add <local path | owner/repo | https://github.com/owner/repo>
nexus repo doctor <name>        # onboarding checks (git health, valid profile)
nexus repo inspect <name>       # trust, languages, validation profile, protected paths
```

Remote sources are cloned through the `git-clone` execution profile into `~/.nexus/repos/`. Registration detects languages and toolchains and applies a suggested validation profile — suggestions still require raised trust before anything executes. See [VALIDATION-PROFILES.md](VALIDATION-PROFILES.md) for configuring what runs.

## What is not implemented

Containerized validation (running untrusted repository scripts inside a container instead of gating them) is planned, not implemented. Until then the approval gate is the control, and the honest statement is: untrusted repository code either does not run on this host, or runs only after the owner explicitly said yes for that task.
