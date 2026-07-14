# Security

This document summarizes the security posture of the Nexus repository. The full threat model, including attack scenarios and honestly stated residual risks, is in [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

## Posture summary

- **Local-first.** The control plane API binds to 127.0.0.1:8400 and PostgreSQL to 127.0.0.1:5442. Nothing listens on a public interface. Exposing a service publicly is an always-gated action (see below). There is currently no authentication on the localhost API; it is a single-owner placeholder and a known residual risk until auth lands.
- **Model output is untrusted input.** Anything a worker (Claude Code, Codex CLI) produces — text, file paths, suggested commands — is treated as untrusted. Paths are validated against the workspace, commands go through an argv allowlist, and success claims are verified by independent validation.
- **Official interfaces only.** Workers are invoked through their official non-interactive CLIs under the owner's existing subscriptions. No browser automation of Claude or ChatGPT, no scraped tokens, no reverse-engineered endpoints.
- **Least privilege.** Planning and review tasks run with read-only tool sets (`--allowedTools "Read Grep Glob"` for Claude Code, `--sandbox read-only` for Codex). Implementation tasks get write access limited to their workspace. The Claude adapter never uses `--dangerously-skip-permissions`; the Codex adapter never uses `--sandbox danger-full-access`.

## Secret handling

- Tokens, keys, and `.env` files are never committed. `.env` is gitignored; `.env.example` contains names only.
- `nexus notion setup` writes the Notion token to `.env` with permissions 600. The token is never logged, never persisted to the database, and never included in prompts.
- GitHub access uses the already-authenticated `gh` CLI; Nexus never handles a GitHub token directly.
- Structured logging applies aggressive redaction (`src/nexus/observability.py`): GitHub token patterns, Notion tokens, `sk-` API keys, Bearer headers, JWTs, and any key named like `token`, `secret`, `password`, `api_key`, or `authorization`. Redaction applies to logs, persisted run events, audit metadata, and captured command output.
- Child processes receive a filtered environment (an explicit passthrough list in `src/nexus/policies/command.py`), so ambient secrets do not leak into worker or validation subprocesses.

## Command execution policy

All subprocess execution driven by validation flows through `CommandExecutor` (`src/nexus/policies/command.py`), which is implemented and tested:

- argv lists only — no shell strings, no interpolation of untrusted text;
- allowlisted executables only (git, gh, claude, codex, uv, pytest, make, node, npm, and a small set of others);
- destructive patterns refused regardless of allowlist: `git push --force`/`-f`, `git reset --hard`, `git clean -fd`, `rm -rf`, `docker system prune`;
- working directory must be at or under the permitted workspace;
- timeouts (default 600 s) and output caps (512 KB) enforced;
- stdout/stderr redacted before storage; argv, exit code, and duration audited.

## Always-gated actions

The following action kinds always require explicit owner approval (`src/nexus/policies/approval.py`), regardless of risk rating or autonomy level:

merge-to-main, production-deploy, enable-paid-service, delete-repository, delete-branch-with-unique-work, delete-cloud-resource, delete-database, delete-notion-content, destructive-migration, change-auth-architecture, publish-externally, modify-dns, purchase-domain, access-unrelated-directory, send-external-communication, expose-service-publicly, rotate-owner-credentials, danger-full-access, disable-security-validation, force-push, change-billing.

Unknown action kinds with high risk fail safe (approval required). Note honestly: the policy engine, approvals API, and dashboard approvals page exist today, but the orchestrator does not yet create approval rows automatically mid-execution; that wiring is planned ([docs/ROADMAP.md](docs/ROADMAP.md)).

## Reporting

This is a private, single-owner repository. If you discover a security issue while working in it (human or coding agent), do not exploit it and do not paper over it: open a GitHub issue labeled `type:security` describing the weakness and affected files, or record it directly for the owner. Never include live tokens or secret values in the report.
