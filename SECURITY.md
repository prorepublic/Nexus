# Security

This document summarizes the security posture of the Nexus repository. The full threat model, including attack scenarios and honestly stated residual risks, is in [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

## Posture summary

Acceptance-pass hardening (2026-07-20):

- Worker prompts are delivered via stdin, never argv: process listings, logs,
  audit rows, and exceptions only ever see a sanitized argv summary plus a
  SHA-256 prompt digest. Captured output is redacted before persistence.
- Execution profiles enforce operation-level command schemas: read-only
  profiles reject every mutating variant (git config/branch/worktree
  mutation, auth login, npm config set, gh merge/approve/ready) regardless
  of flag order, aliases, or `--flag=value` form; unknown flags fail closed.
- Path arguments are confined: `--add-dir`, `--cd`, worktree paths, and any
  absolute or `..` path argument must resolve inside the permitted roots
  (symlink-safe); `git -C` / `--git-dir` overrides are prohibited outright.
- State-changing API requests require the generated local-owner token
  (constant-time comparison); the dashboard uses a same-origin server-side
  proxy so the token never reaches browser JavaScript.


- **Local-first.** The control plane API binds to 127.0.0.1:8400 and PostgreSQL to 127.0.0.1:5442. Nothing listens on a public interface; exposing a service publicly is an always-gated action. The API additionally enforces local-owner protection ([ADR-013](docs/adr/ADR-013-local-owner-api-protection.md)): a Host-header allowlist (localhost/127.0.0.1, defeating DNS rebinding), a required `X-Nexus-Client` header on all state-changing requests (forcing a CORS preflight so hostile web pages cannot fire-and-forget form POSTs), and CORS restricted to localhost:3400. There is still no user authentication on the localhost API; any local process can call it — a documented residual risk until auth lands.
- **Model output is untrusted input.** Anything a worker (Claude Code, Codex CLI) produces — text, file paths, suggested commands, plans, review verdicts, success claims — is treated as untrusted. Paths are confined to the workspace (traversal, absolute-path, and symlink escapes rejected), plans and verdicts are schema-validated and normalized defensively, and success claims are verified by independent fail-closed validation plus cross-agent review.
- **Official interfaces only.** Workers are invoked through their official non-interactive CLIs under the owner's existing subscriptions. No browser automation of Claude or ChatGPT, no scraped tokens, no reverse-engineered endpoints.
- **Least privilege.** Planning and review tasks run read-only (`--allowedTools "Read Grep Glob"` for Claude Code, `--sandbox read-only` for Codex). Implementation tasks get write access limited to their worktree. Permission-bypass flags (`--dangerously-skip-permissions`, `danger-full-access`) are refused by the execution profiles themselves, so an adapter regression cannot reintroduce them.
- **Repository code does not run on the host by default.** Registered repositories start `untrusted`; repository-defined validation commands execute only at raised trust levels or behind an explicit `run-untrusted-repository-scripts` approval gate. See [docs/REPOSITORY-TRUST.md](docs/REPOSITORY-TRUST.md).

## Secret handling

- Tokens, keys, and `.env` files are never committed. `.env` is gitignored; `.env.example` contains names only. CI runs `scripts/secret-scan.sh` plus `pip-audit` and `npm audit`.
- `nexus notion setup` writes the Notion token to `.env` with permissions 600. The token is never logged, never persisted to the database, and never included in prompts.
- GitHub access uses the already-authenticated `gh` CLI; Nexus never handles a GitHub token directly.
- Structured logging applies aggressive redaction (`src/nexus/observability.py`): GitHub token patterns, Notion tokens, `sk-` API keys, Bearer headers, JWTs, and any key named like `token`, `secret`, `password`, `api_key`, or `authorization`. Redaction applies to logs, persisted run events, prompts, audit metadata, captured command output, and everything mirrored to Notion or posted to GitHub.
- Child processes receive a filtered environment: each execution profile declares an explicit passthrough list (`src/nexus/execution/profiles.py`), so ambient secrets do not leak into worker or validation subprocesses.
- The built-in `secret-scan` validation check inspects changed files for obvious secret material before a task can complete.

## Command execution

All subprocess execution flows through the centralized execution subsystem (`src/nexus/execution/`, [ADR-006](docs/adr/ADR-006-centralized-execution-subsystem.md)); a static test fails the build if direct `subprocess` use appears anywhere else:

- 15 purpose-specific profiles (health checks, worker runs, git clone/read/worktree/commit/push, GitHub read/write-safe, trusted/untrusted validation, migrations, tool install, launchd), each with its own executables, permitted subcommands, prohibited tokens, timeout, output budget, and environment surface;
- argv lists only — no shell strings, no interpolation of untrusted text;
- prohibited tokens refused in any position, including `--flag=value` forms: git force variants, `--hard`, `--mirror`, `--no-verify`, worker permission bypasses, and gh merge/approve/admin flags;
- `gh pr merge` and equivalents are unreachable: merge is not a permitted verb in any profile;
- working directories confined to permitted roots; `..` traversal, absolute escape, and symlink escape rejected;
- timeouts terminate the whole process group (SIGTERM, then SIGKILL); output caps and redaction before storage; every execution audited in `command_executions`;
- the `validation-untrusted` profile permits nothing on the host — untrusted repository scripts require an approval gate.

## Always-gated actions

Action kinds in `OWNER_APPROVAL_REQUIRED` (`src/nexus/policies/approval.py`) always require explicit owner approval regardless of risk rating or autonomy level: merge-to-main, production-deploy, enable-paid-service, delete-repository, delete-branch-with-unique-work, delete-cloud-resource, delete-database, delete-notion-content, destructive-migration, change-auth-architecture, publish-externally, modify-dns, purchase-domain, access-unrelated-directory, send-external-communication, expose-service-publicly, rotate-owner-credentials, danger-full-access, disable-security-validation, force-push, change-billing.

The engine now creates approval gates mid-execution and enforces them: `approve-plan` (manual-autonomy goals execute nothing until the plan is approved), `run-untrusted-repository-scripts` (blocks the task, resumes exactly at validation on approval), and `single-worker-review-fallback` (under the `required` review policy). Blocked work resumes deterministically on approval; denial cancels it and is never re-asked. See [docs/AUTONOMY-AND-APPROVALS.md](docs/AUTONOMY-AND-APPROVALS.md).

## Reporting

This is a private, single-owner repository. If you discover a security issue while working in it (human or coding agent), do not exploit it and do not paper over it: open a GitHub issue labeled `type:security` describing the weakness and affected files, or record it directly for the owner. Never include live tokens or secret values in the report.
