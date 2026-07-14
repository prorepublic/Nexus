# GitHub Integration

How Nexus interacts with GitHub. Source: `src/nexus/adapters/github.py`, implemented with an injectable runner and tested with fakes.

## Approach: the authenticated gh CLI

All GitHub operations go through the locally installed, already-authenticated `gh` CLI. Nexus never handles a GitHub token: no token storage, no token in `.env`, no scraping. Authentication state is read via `gh auth status` and reported by `nexus doctor`. Calls are argv lists (no shell strings); the target repo comes from `NEXUS_GITHUB_REPO` or per-adapter override.

## Capabilities (implemented)

| Capability | Detail |
| --- | --- |
| Create issues | `create_issue(title, body, labels)` |
| Comment on issues | `comment_issue(number, body)` |
| Create pull requests | `create_pull_request(...)` — **draft by default** |
| Post validation summaries | PR comment with the evidence for a task branch |
| Read PR review comments | `get_pr_review_comments(number)` — reviews plus comments |
| Label bootstrap | `ensure_labels()` — idempotent creation of the 17 Nexus labels |
| Push task branches | `push_branch(worktree, branch)` — plain `git push -u origin`, **never force** |

## Planned (not implemented)

- Translating PR review comments into follow-up Nexus tasks (reading is implemented; translation is not — [ROADMAP.md](ROADMAP.md) Milestone 2).
- GitHub Actions-based PR validation (running `make validate` in CI on Nexus-opened PRs).
- `infra/github/` is reserved for repo-setup scripts; labels are bootstrapped via this adapter today.

## The 17 labels

| Label | Meaning |
| --- | --- |
| `type:goal` | Issue tracks a Nexus goal |
| `type:feature` | Feature work |
| `type:bug` | Defect |
| `type:architecture` | Architectural change; expect an ADR |
| `type:security` | Security-relevant change or report |
| `type:documentation` | Documentation work |
| `status:ready` | Ready to be picked up |
| `status:running` | A worker is executing this |
| `status:blocked` | Waiting on a dependency or approval |
| `status:review` | Awaiting owner/cross-agent review |
| `status:done` | Delivered and validated |
| `worker:claude` | Routed to Claude Code |
| `worker:codex` | Routed to Codex CLI |
| `worker:auto` | Routing left to the rules-based router |
| `risk:low` / `risk:medium` / `risk:high` | Risk classification per the approval policy |
| `approval:required` | Blocked on an explicit owner approval gate |

## Branch naming

Task branches are deterministic: `nexus/<goal8>/<task8>` (last 8 characters of the goal and task IDs), created by the workspace module in isolated worktrees. See [OPERATING-MODEL.md](OPERATING-MODEL.md) section 6.

## Hard rules

- **Never force-push.** Refused at three layers: the adapter has no force path, `CommandPolicy` blocks `git push --force`/`-f` by pattern, and `force-push` is an always-gated approval action.
- **Never auto-merge.** PRs open as drafts; `merge-to-main` always requires owner approval ([AUTONOMY-AND-APPROVALS.md](AUTONOMY-AND-APPROVALS.md)).
- Never delete branches with unique work; never rewrite shared history.
