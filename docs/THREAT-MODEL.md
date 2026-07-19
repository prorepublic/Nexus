# Threat Model

What Nexus protects, where the trust boundaries sit, the attack scenarios considered, and — honestly — what remains unmitigated. Companion to [../SECURITY.md](../SECURITY.md).

## Assets

| Asset | Why it matters |
| --- | --- |
| Source code (this repo and any repository Nexus operates on) | Integrity of everything Nexus delivers |
| Credentials | gh CLI auth, Notion internal integration token, worker CLI sessions |
| Subscriptions | Claude Max and ChatGPT Plus usage budgets — the only money at stake |
| The owner's machine | The control plane, database, and worker CLIs all run here with the owner's local privileges |

## Trust boundaries

- **Model output is untrusted.** Everything a worker produces — text, file paths, suggested commands, success claims — crosses a trust boundary into Nexus. It is path-validated, allowlist-filtered, redacted, size-capped, and independently verified.
- **Worker CLIs are semi-trusted.** Claude Code and Codex are official vendor tools invoked with constrained flags (read-only tool sets or sandboxes for planning/review, workspace-limited access, bounded turns, never `--dangerously-skip-permissions` / `danger-full-access`). But they are real programs running under the owner's account with their own permission models — Nexus constrains how it invokes them, not what they are ultimately capable of.
- **Localhost binding is the network boundary.** API on 127.0.0.1:8400, Postgres on 127.0.0.1:5442, CORS restricted to localhost:3400. Nothing is reachable off-machine; exposing a service publicly is an always-gated action.
- **Repository content is untrusted input to workers.** Files a worker reads may contain adversarial instructions (prompt injection).

## Attack scenarios and mitigations

### 1. Prompt injection via repository content

A file in the workspace instructs the worker to exfiltrate secrets, modify unrelated code, or run destructive commands.

Mitigations: argv allowlists and destructive-pattern refusal in `CommandPolicy` (no shell strings, cwd confined to the workspace); workspace confinement in adapter flags (`--add-dir`, `--cd`, sandbox modes); read-only execution for planning and review tasks; evidence-based validation so injected "success" claims do not complete tasks; approval gates on all high-consequence actions; filtered child environment so there are few secrets to steal in the first place.

### 2. Secret exfiltration

A worker, log pipeline, or command output leaks a token into the database, a log file, a prompt, or a commit.

Mitigations: redaction patterns (GitHub/Notion tokens, `sk-` keys, Bearer values, JWTs, secret-like key names) applied to logs, run events, audit metadata, and captured command output; the Notion token lives only in `.env` (chmod 600, gitignored) and is never logged or passed to prompts; GitHub auth stays inside `gh`, Nexus never touches the token; `ENV_PASSTHROUGH` allowlist keeps ambient secrets out of child processes; `.env.example` contains names only.

### 3. Runaway autonomy

A repair loop thrashes, a worker hangs, or tasks cascade beyond what the owner intended.

Mitigations: finite budgets everywhere — max 3 attempts per task, 1800 s task timeout, 600 s command timeout, 512 KB output caps, bounded turns; explicit state machines where illegal transitions raise instead of corrupting state; dependency cancellation so failed goals settle instead of spinning; cancellation at run and goal level; full audit trail; approval gates for irreversible actions (policy engine implemented; automatic gate creation mid-execution is still planned — see residual risks).

### 4. Destructive operations against git history or infrastructure

Mitigations: `git push --force`, `git reset --hard`, `git clean -fd`, `rm -rf`, `docker system prune` refused by pattern regardless of allowlist; worktree isolation with baseline commits and diff capture; worktree removal refused while uncommitted changes exist; the GitHub adapter never force-pushes and only opens draft PRs; merge-to-main, destructive migrations, and all delete-* actions are always-gated.

## Residual risks (stated honestly)

- **Worker CLIs have broad local access under their own permission models.** Nexus invokes them with constrained flags, but a vendor CLI bug or misbehavior could still act beyond the workspace. This is accepted for the bootstrap phase; it is why review tasks are read-only and why cross-review exists.
- **No authentication on the localhost API yet.** Any local process on the machine can call it. Acceptable for a single-owner machine, but real; auth (potentially Entra ID) is planned.
- **Approval gates are not yet created automatically mid-execution.** The always-gated list is enforced as policy code and surfaced via API/dashboard, but the orchestrator does not yet pause tasks on gated actions. Until that wiring lands (Milestone 1), the practical guardrails are the command policy, read-only modes, and the fact that no implemented code path performs gated actions autonomously.
- **Redaction is pattern-based.** A secret in an unrecognized format could evade it. Patterns are extended as new secret shapes appear.
- **`.env` on disk (chmod 600) rather than the OS keychain.** Keychain storage is planned.
- **Dev-default database credentials** (`nexus`/`nexus_local_dev`) on a localhost-only port; overridable via `.env`, and the database holds no owner credentials.
