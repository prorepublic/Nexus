# Autonomy and Approvals

How much Nexus may do on its own, and where the owner must decide. Source: `src/nexus/policies/approval.py` (implemented and unit-tested).

## Autonomy levels

Set per goal (`goal.autonomy`):

| Level | Behavior |
| --- | --- |
| `bounded` (default) | Tasks run automatically within policy limits (timeouts, repair budgets, command policy, always-gated actions). |
| `manual` | Every code-writing task requires approval before execution; unknown actions also gate. Maximum-caution mode. |

Either way, autonomy is finite: the numeric limits in [OPERATING-MODEL.md](OPERATING-MODEL.md) apply to every run.

## Always require owner approval

`OWNER_APPROVAL_REQUIRED` — these action kinds gate regardless of risk rating or autonomy level:

- merge-to-main
- production-deploy
- enable-paid-service
- delete-repository
- delete-branch-with-unique-work
- delete-cloud-resource
- delete-database
- delete-notion-content
- destructive-migration
- change-auth-architecture
- publish-externally
- modify-dns
- purchase-domain
- access-unrelated-directory
- send-external-communication
- expose-service-publicly
- rotate-owner-credentials
- danger-full-access
- disable-security-validation
- force-push
- change-billing

## Never interrupt the owner

`NO_APPROVAL_REQUIRED` — routine development actions that must never create a gate:

- create-branch
- create-worktree
- write-code-in-workspace (except under `manual` autonomy)
- run-tests
- run-linters
- create-documentation
- create-github-issue
- open-draft-pr
- update-nexus-notion-pages
- safe-dev-migration
- bounded-repair

## Unknown actions fail safe

An action kind on neither list is decided by risk and autonomy: high risk requires approval; `manual` autonomy requires approval; otherwise it proceeds. The bias is toward gating anything both unknown and consequential.

## How decisions surface

- **API:** `GET /api/approvals?state=pending` lists gates; `POST /api/approvals/{id}/decision` records `approved` or `denied` with an optional note. Decisions are audit-logged; a non-pending approval cannot be re-decided.
- **Dashboard:** the approvals page (localhost:3400) shows pending gates with kind, description, and risk, and offers approve/deny. The dashboard header also shows the pending-approvals count.
- Approval rows live in the `approvals` table with states pending / approved / denied / expired.

## Honest status note

The policy engine (`approval_required(...)`), the `approvals` table, the API endpoints, and the dashboard page are all implemented and tested. **What is not yet wired: the orchestrator does not automatically create approval rows when a gated action arises mid-execution.** Creating gates from the execution path — pausing the task in `blocked`, resuming on approval — is scaffolded/planned (Milestone 1, [ROADMAP.md](ROADMAP.md)). Until then, no implemented code path performs always-gated actions autonomously; the practical backstops are the command policy, read-only review modes, and draft-only PRs ([THREAT-MODEL.md](THREAT-MODEL.md), residual risks).
