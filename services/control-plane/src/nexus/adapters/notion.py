"""Notion adapter (ADR-004).

Official Notion REST API through an internal integration token — works on the
Notion Free plan, no premium MCP access assumed. The token is read from the
local environment (NEXUS_NOTION_TOKEN, set up via `nexus notion setup`) and is
never logged, persisted to the database, or included in prompts.

The workspace bootstrap is idempotent: pages and databases are looked up by
title under the parent page before anything is created, and every created
object is recorded as an ExternalSync row so reruns reconcile instead of
duplicating.
"""

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from nexus.config import get_settings
from nexus.observability import get_logger

log = get_logger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

WORKSPACE_PAGES = [
    "Vision",
    "Architecture",
    "Roadmap",
    "Backlog",
    "Agent Catalog",
    "Decisions",
    "Knowledge Base",
    "Research",
    "Meeting Notes",
]

GOALS_DB_SCHEMA: dict[str, Any] = {
    "Goal ID": {"rich_text": {}},
    "Title": {"title": {}},
    "Description": {"rich_text": {}},
    "Status": {
        "select": {
            "options": [
                {"name": s}
                for s in [
                    "draft",
                    "planning",
                    "ready",
                    "executing",
                    "blocked",
                    "review",
                    "completed",
                    "failed",
                    "cancelled",
                ]
            ]
        }
    },
    "Priority": {"select": {"options": [{"name": p} for p in ["low", "normal", "high"]]}},
    "Repository": {"rich_text": {}},
    "Requested Worker": {
        "select": {"options": [{"name": w} for w in ["claude-code", "codex-cli", "fake", "auto"]]}
    },
    "Selected Worker": {"rich_text": {}},
    "Created": {"date": {}},
    "Updated": {"date": {}},
    "GitHub Link": {"url": {}},
    "Nexus Link": {"url": {}},
}

TASKS_DB_SCHEMA: dict[str, Any] = {
    "Task ID": {"rich_text": {}},
    "Goal": {"rich_text": {}},
    "Title": {"title": {}},
    "Status": {
        "select": {
            "options": [
                {"name": s}
                for s in [
                    "pending",
                    "ready",
                    "queued",
                    "running",
                    "validating",
                    "repairing",
                    "review",
                    "blocked",
                    "completed",
                    "failed",
                    "cancelled",
                ]
            ]
        }
    },
    "Worker": {"rich_text": {}},
    "Reviewer": {"rich_text": {}},
    "Risk": {"select": {"options": [{"name": r} for r in ["low", "medium", "high"]]}},
    "Dependencies": {"rich_text": {}},
    "Branch": {"rich_text": {}},
    "Pull Request": {"url": {}},
    "Attempt Count": {"number": {}},
    "Started": {"date": {}},
    "Completed": {"date": {}},
}

DECISIONS_DB_SCHEMA: dict[str, Any] = {
    "ADR ID": {"rich_text": {}},
    "Title": {"title": {}},
    "Status": {
        "select": {
            "options": [{"name": s} for s in ["proposed", "accepted", "superseded", "deprecated"]]
        }
    },
    "Decision Date": {"date": {}},
    "Context": {"rich_text": {}},
    "Decision": {"rich_text": {}},
    "Consequences": {"rich_text": {}},
    "Related Goal": {"rich_text": {}},
    "GitHub Link": {"url": {}},
}

AGENTS_DB_SCHEMA: dict[str, Any] = {
    "Agent Name": {"title": {}},
    "Provider": {"rich_text": {}},
    "Role": {"rich_text": {}},
    "Capabilities": {"rich_text": {}},
    "Enabled": {"checkbox": {}},
    "Health": {"rich_text": {}},
    "Cost Mode": {"rich_text": {}},
    "Last Checked": {"date": {}},
}

DATABASES: dict[str, dict[str, Any]] = {
    "Goals": GOALS_DB_SCHEMA,
    "Tasks": TASKS_DB_SCHEMA,
    "Decisions": DECISIONS_DB_SCHEMA,
    "Agents": AGENTS_DB_SCHEMA,
}


class NotionError(Exception):
    pass


class NotionNotConfigured(NotionError):
    pass


def normalize_page_id(raw: str) -> str:
    """Accept a bare ID, dashed ID, or a Notion page URL."""
    candidate = raw.strip().rstrip("/").split("/")[-1].split("?")[0]
    candidate = candidate.rsplit("-", 1)[-1] if "-" in candidate else candidate
    compact = candidate.replace("-", "")
    if len(compact) != 32:
        raise NotionError(f"cannot parse Notion page id from: {raw!r}")
    return f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:32]}"


@dataclass
class BootstrapReport:
    created_pages: list[str] = field(default_factory=list)
    existing_pages: list[str] = field(default_factory=list)
    created_databases: list[str] = field(default_factory=list)
    existing_databases: list[str] = field(default_factory=list)
    home_page_id: str | None = None


class NotionClient:
    """Thin REST client with rate-limit handling and bounded retries."""

    def __init__(
        self,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 4,
    ) -> None:
        settings = get_settings()
        self.token = token or settings.notion_token
        if not self.token:
            raise NotionNotConfigured(
                "Notion token is not configured. Run `nexus notion setup` first."
            )
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=NOTION_API,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30,
            transport=transport,
        )

    def request(
        self, method: str, path: str, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            attempt += 1
            response = self._client.request(method, path, json=json_body)
            if response.status_code == 429 and attempt <= self.max_retries:
                delay = float(response.headers.get("Retry-After", "1"))
                log.info("notion.rate-limited", retry_after=delay, attempt=attempt)
                time.sleep(min(delay, 30))
                continue
            if response.status_code >= 500 and attempt <= self.max_retries:
                time.sleep(min(2**attempt, 15))
                continue
            if response.status_code >= 400:
                raise NotionError(
                    f"Notion API {method} {path} -> {response.status_code}: {response.text[:300]}"
                )
            return response.json()

    # -- lookups ------------------------------------------------------------
    def list_child_blocks(self, page_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            path = f"/blocks/{page_id}/children?page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            data = self.request("GET", path)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor")

    def find_child_by_title(self, parent_id: str, title: str, kind: str) -> str | None:
        """kind: 'child_page' or 'child_database'. Returns block/page id."""
        for block in self.list_child_blocks(parent_id):
            if block.get("type") != kind:
                continue
            block_title = block.get(kind, {}).get("title", "")
            if block_title == title:
                return str(block["id"])
        return None

    # -- creation -----------------------------------------------------------
    def create_page(
        self, parent_id: str, title: str, children: list[dict[str, Any]] | None = None
    ) -> str:
        data = self.request(
            "POST",
            "/pages",
            {
                "parent": {"page_id": parent_id},
                "properties": {"title": {"title": [{"text": {"content": title}}]}},
                "children": children or [],
            },
        )
        return str(data["id"])

    def create_database(self, parent_id: str, title: str, schema: dict[str, Any]) -> str:
        data = self.request(
            "POST",
            "/databases",
            {
                "parent": {"type": "page_id", "page_id": parent_id},
                "title": [{"type": "text", "text": {"content": title}}],
                "properties": schema,
            },
        )
        return str(data["id"])

    def close(self) -> None:
        self._client.close()


def bootstrap_workspace(client: NotionClient, parent_page: str) -> BootstrapReport:
    """Create or reconcile the Nexus Notion workspace. Safe to rerun."""
    report = BootstrapReport()
    parent_id = normalize_page_id(parent_page)

    home_id = client.find_child_by_title(parent_id, "Nexus Home", "child_page")
    if home_id is None:
        home_id = client.create_page(
            parent_id,
            "Nexus Home",
            children=[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": "Nexus planning and knowledge workspace. Generated "
                                    "and "
                                    "maintained by `nexus notion bootstrap`. GitHub remains "
                                    "authoritative for code; Nexus PostgreSQL for live "
                                    "execution state."
                                },
                            }
                        ]
                    },
                }
            ],
        )
        report.created_pages.append("Nexus Home")
    else:
        report.existing_pages.append("Nexus Home")
    report.home_page_id = home_id

    for title in WORKSPACE_PAGES:
        existing = client.find_child_by_title(home_id, title, "child_page")
        if existing is None:
            client.create_page(home_id, title)
            report.created_pages.append(title)
        else:
            report.existing_pages.append(title)

    for title, schema in DATABASES.items():
        existing = client.find_child_by_title(home_id, title, "child_database")
        if existing is None:
            client.create_database(home_id, title, schema)
            report.created_databases.append(title)
        else:
            report.existing_databases.append(title)

    return report


def record_sync(
    session,
    system: str,
    local_kind: str,
    local_key: str,
    remote_id: str,
    remote_url: str | None = None,
) -> None:
    """Upsert an ExternalSync record for an object mirrored to Notion/GitHub."""
    from sqlalchemy import select

    from nexus.db.models import ExternalSync, utcnow

    existing = session.scalars(
        select(ExternalSync).where(
            ExternalSync.system == system,
            ExternalSync.local_kind == local_kind,
            ExternalSync.local_key == local_key,
        )
    ).first()
    if existing is None:
        session.add(
            ExternalSync(
                system=system,
                local_kind=local_kind,
                local_key=local_key,
                remote_id=remote_id,
                remote_url=remote_url,
                last_synced_at=utcnow(),
            )
        )
    else:
        existing.remote_id = remote_id
        existing.remote_url = remote_url
        existing.last_synced_at = utcnow()
