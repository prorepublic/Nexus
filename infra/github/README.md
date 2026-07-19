# infra/github

Reserved for repository-setup scripts (branch protection, repo configuration) if they become needed. GitHub labels are not managed from here: the 17 nexus labels are bootstrapped idempotently via the GitHub adapter and the `gh` CLI (`src/nexus/adapters/github.py`, `ensure_labels`) — see [../../docs/GITHUB-INTEGRATION.md](../../docs/GITHUB-INTEGRATION.md). Nothing here is used today.
