# Notion Integration

How Nexus mirrors its planning and knowledge surface into Notion. Design decision: [adr/ADR-004-notion-rest-integration.md](adr/ADR-004-notion-rest-integration.md). Source: `src/nexus/adapters/notion.py`, tested against an in-memory fake Notion.

## Design

- **Official REST API v1** (`https://api.notion.com/v1`, version header `2022-06-28`) with an **internal integration token**. This works on the **Notion Free plan** — no premium plan, no premium MCP connector, consistent with [COST-GUARDRAILS.md](COST-GUARDRAILS.md).
- Roles are explicit: GitHub remains authoritative for code, Nexus PostgreSQL for live execution state, Notion for planning and knowledge.
- The token lives only in the local `.env` (chmod 600, gitignored) and is never logged, committed, persisted to the database, or passed to worker prompts.

## Commands

### `nexus notion setup` (implemented)

Interactive: prompts for the internal integration token (hidden input) and the parent page URL or ID, then writes `NEXUS_NOTION_TOKEN` and `NEXUS_NOTION_PARENT_PAGE` into `./.env`, replacing prior values and setting file permissions to 600.

### `nexus notion bootstrap` (implemented, idempotent)

Creates or reconciles, under the parent page:

- **Nexus Home** page (with an explanatory paragraph), and beneath it:
- Child pages: Vision, Architecture, Roadmap, Backlog, Agent Catalog, Decisions, Knowledge Base, Research, Meeting Notes.
- Databases: Goals, Tasks, Decisions, Agents (schemas below).

Reruns report created vs already-existing objects and never duplicate.

### `nexus notion sync` (scaffolded, not implemented)

Intended to push current goal and task records into the Goals and Tasks databases. Today it exits with code 2 and a clear message. The plumbing exists: the `external_syncs` table and the `record_sync` upsert helper are implemented and tested. See [ROADMAP.md](ROADMAP.md), Milestone 2.

## Idempotency mechanism

Two layers:

1. **Title lookup under the parent.** Before creating anything, the adapter lists child blocks of the parent (paginated) and looks for a `child_page` / `child_database` with the exact title. Existing objects are reused.
2. **ExternalSync upserts.** `record_sync(system, local_kind, local_key, remote_id, remote_url)` records each mirrored object in `external_syncs`, keyed by (system, local_kind, local_key), updating in place on re-sync. This is what future record sync will use to map goals/tasks to Notion pages stably.

## Database schemas

**Goals** — Goal ID (rich_text), Title (title), Description (rich_text), Status (select: draft, planning, ready, executing, blocked, review, completed, failed, cancelled), Priority (select: low, normal, high), Repository (rich_text), Requested Worker (select: claude-code, codex-cli, fake, auto), Selected Worker (rich_text), Created (date), Updated (date), GitHub Link (url), Nexus Link (url).

**Tasks** — Task ID (rich_text), Goal (rich_text), Title (title), Status (select: pending, ready, queued, running, validating, repairing, review, blocked, completed, failed, cancelled), Worker (rich_text), Reviewer (rich_text), Risk (select: low, medium, high), Dependencies (rich_text), Branch (rich_text), Pull Request (url), Attempt Count (number), Started (date), Completed (date).

**Decisions** — ADR ID (rich_text), Title (title), Status (select: proposed, accepted, superseded, deprecated), Decision Date (date), Context (rich_text), Decision (rich_text), Consequences (rich_text), Related Goal (rich_text), GitHub Link (url).

**Agents** — Agent Name (title), Provider (rich_text), Role (rich_text), Capabilities (rich_text), Enabled (checkbox), Health (rich_text), Cost Mode (rich_text), Last Checked (date).

## Rate-limit and error handling

- HTTP 429: honors the `Retry-After` header (capped at 30 s), retries up to 4 times.
- HTTP 5xx: exponential backoff (capped at 15 s), same retry budget.
- Other 4xx: raises `NotionError` with a truncated response body; nothing is silently swallowed.
- Missing configuration raises `NotionNotConfigured` with the instruction to run `nexus notion setup`.

## Owner setup steps

1. Open https://www.notion.so/my-integrations and create a new **internal** integration in your workspace. Copy the secret token (starts with `ntn_`; this pattern is in the redaction list).
2. In Notion, create (or pick) a parent page for Nexus, e.g. "Nexus".
3. Share that page with the integration: page menu → Connections → add your integration. Without this the API returns 404 for the parent.
4. In the repo: `cd services/control-plane && uv run nexus notion setup` and paste the token and the parent page URL.
5. Run `uv run nexus notion bootstrap`. Rerun any time; it reconciles instead of duplicating.
6. `uv run nexus doctor` should now show `notion configured`.
