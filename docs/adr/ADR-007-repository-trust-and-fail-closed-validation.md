# ADR-007: Repository trust levels and fail-closed validation

- Status: accepted
- Date: 2026-07-15

## Context

The bootstrap validation runner executed `make test|lint|typecheck|build` whenever a Makefile was present, and treated "no automated check defined" as a skip that did not block completion. Both behaviors are unsafe once Nexus operates on arbitrary repositories: a repository's Makefile is repository-controlled code, so running it on the host hands host execution to whoever wrote the repository; and skip-equals-pass lets a task complete with no evidence at all. Repository-backed goals also executed without any onboarding, so nothing guaranteed the repository was even inspectable.

## Decision

**Validation fails closed** (`src/nexus/services/validation.py`):

- `ValidationStatus` has five states: `passed`, `failed`, `skipped`, `blocked`, `error`. Only `passed` counts toward completion; `skipped` and `blocked` never pass.
- A requested check with no configured command is **blocked**, not skipped.
- An empty validation set never completes an implementation, refactoring, or testing task (`evidence_sufficient`).
- For an implementation task in a registered repository, `files-exist` (and `changed-scope`) alone are insufficient; at least one command-backed or content-inspecting check must pass.
- Expected-file paths are confined to the workspace; escapes are `error`, never ignored.

**Validation commands come from Nexus, not the repository.** Each registered repository carries a validation profile stored in PostgreSQL (`repositories.validation_profile`), managed with `nexus repo validation set|show` and schema-checked by `validate_profile_config` before persistence: known validation kinds only, argv lists only, and every command must already satisfy the `validation-trusted` execution profile, so a malicious profile cannot smuggle in an unlisted executable. Repository Makefiles are never run implicitly.

**Repository trust is explicit.** Trust levels: `untrusted` (default) -> `reviewed` -> `trusted-local` -> `trusted-owner-approved`. Only `trusted-local` and `trusted-owner-approved` permit repository-defined scripts to execute on the host. For lower trust, command-backed checks come back `blocked`; the engine then creates a `run-untrusted-repository-scripts` approval gate, moves the task to `blocked`, and on approval resumes exactly at the validation stage (a `resume_stage` marker on the task, which does not consume an extra worker attempt). Denial cancels the task. Trust is raised deliberately by the owner (`nexus repo trust <name> <level>`, or the dashboard repositories page) and every change is audited.

**Built-in checks execute no repository code:** `files-exist`, `changed-scope` (diff of changed files against the task's declared scope globs), and `secret-scan` (pattern scan of changed files) run in-process regardless of trust level.

**Repository onboarding is required.** Repo-backed goals are rejected unless the repository is registered (`nexus repo add <local path | owner/repo | GitHub URL>`, cloning through the `git-clone` profile into `~/.nexus/repos` when needed). Registration detects languages and toolchains, suggests a validation profile, and records onboarding health; `nexus repo doctor` re-checks it. The orchestrator refuses to create a worktree for a repository that has not passed onboarding.

Containerized validation for untrusted repositories is **not implemented**; the approval gate is the current control (see [../ROADMAP.md](../ROADMAP.md)).

## Consequences

- A task can no longer complete because nothing was checked; absence of evidence is a failure, not a pass.
- Cloning or registering a repository grants Nexus nothing beyond read/inspect; host execution of repository code is a separate, owner-controlled decision with an auditable trail.
- The owner is interrupted exactly once per task for untrusted-script execution, and the decision is remembered (approve resumes at validation; deny cancels — it is never re-asked for the same task).
- Validation configuration is reviewable in one place per repository and survives repository content changes; a PR that edits a Makefile cannot change what Nexus executes.
- Details and examples: [../REPOSITORY-TRUST.md](../REPOSITORY-TRUST.md), [../VALIDATION-PROFILES.md](../VALIDATION-PROFILES.md).
