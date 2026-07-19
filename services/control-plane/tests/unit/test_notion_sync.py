"""Unit tests for the one-way Notion sync engine.

These use an in-memory SQLite session directly (not the db_session fixture,
which needs PostgreSQL) plus the FakeNotion in-memory transport extended with
database-page endpoints.
"""

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from nexus.adapters.notion import NotionClient, bootstrap_workspace
from nexus.db.base import Base
from nexus.db.models import ExternalSync, Goal, Repository, Run, Task
from nexus.services.notion_sync import sync_all

PARENT_ID = "11112222333344445555666677778888"


class FakeNotion:
    """In-memory Notion covering bootstrap plus database-page CRUD."""

    def __init__(self) -> None:
        self.children: dict[str, list[dict[str, Any]]] = {}
        self.databases: dict[str, str] = {}  # title -> database id
        self.db_pages: dict[str, dict[str, Any]] = {}  # page id -> stored page
        self.created_pages = 0
        self.created_databases = 0
        self.created_db_pages = 0
        self.updated_db_pages = 0
        self.fail_page_creates: dict[str, int] = {}  # database id -> failures left

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/v1/blocks/") and path.endswith("/children"):
            parent = path.split("/")[3]
            return httpx.Response(
                200, json={"results": self.children.get(parent, []), "has_more": False}
            )
        if path.startswith("/v1/databases/") and path.endswith("/query"):
            database_id = path.split("/")[3]
            pages = [
                {"id": page_id, "properties": page["properties"]}
                for page_id, page in self.db_pages.items()
                if page["database_id"] == database_id
            ]
            return httpx.Response(200, json={"results": pages, "has_more": False})
        if path == "/v1/pages" and request.method == "POST":
            body = json.loads(request.content)
            parent = body["parent"]
            if "database_id" in parent:
                database_id = parent["database_id"]
                if self.fail_page_creates.get(database_id, 0) > 0:
                    self.fail_page_creates[database_id] -= 1
                    return httpx.Response(400, json={"message": "simulated failure"})
                page_id = f"dbpage-{self.created_db_pages}"
                self.created_db_pages += 1
                self.db_pages[page_id] = {
                    "database_id": database_id,
                    "properties": body["properties"],
                }
                return httpx.Response(200, json={"id": page_id})
            title = body["properties"]["title"]["title"][0]["text"]["content"]
            page_id = f"page-{self.created_pages}"
            self.created_pages += 1
            self.children.setdefault(parent["page_id"], []).append(
                {"id": page_id, "type": "child_page", "child_page": {"title": title}}
            )
            return httpx.Response(200, json={"id": page_id})
        if path.startswith("/v1/pages/") and request.method == "PATCH":
            page_id = path.split("/")[3]
            if page_id not in self.db_pages:
                return httpx.Response(404, json={"message": "page not found"})
            body = json.loads(request.content)
            self.db_pages[page_id]["properties"].update(body["properties"])
            self.updated_db_pages += 1
            return httpx.Response(200, json={"id": page_id})
        if path == "/v1/databases":
            body = json.loads(request.content)
            parent = body["parent"]["page_id"]
            title = body["title"][0]["text"]["content"]
            db_id = f"db-{self.created_databases}"
            self.created_databases += 1
            self.databases[title] = db_id
            self.children.setdefault(parent, []).append(
                {"id": db_id, "type": "child_database", "child_database": {"title": title}}
            )
            return httpx.Response(200, json={"id": db_id})
        return httpx.Response(404, json={"message": "not found"})


def make_client(fake: FakeNotion) -> NotionClient:
    return NotionClient(
        token="ntn_test_token_not_real_aaaaaaaaaaaa", transport=httpx.MockTransport(fake.handler)
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def seed_entities(session: Session, exit_summary: str = "All validations passed.") -> None:
    repo = Repository(name="acme/platform", remote_url=None)
    session.add(repo)
    session.flush()
    goal = Goal(
        title="Harden CI pipeline",
        description="Add secret scanning to the pipeline.",
        repository_id=repo.id,
    )
    session.add(goal)
    session.flush()
    task = Task(goal_id=goal.id, title="Add gitleaks step", instruction="Wire gitleaks into CI.")
    session.add(task)
    session.flush()
    session.add(Run(task_id=task.id, worker="fake", exit_summary=exit_summary))
    session.commit()


def sync_count(session: Session) -> int:
    return len(session.scalars(select(ExternalSync)).all())


class TestSyncAll:
    def test_full_sync_creates_pages(self, session):
        seed_entities(session)
        fake = FakeNotion()

        report = sync_all(session, make_client(fake), PARENT_ID)

        assert report.created == {"repository": 1, "goal": 1, "task": 1, "run": 1}
        assert report.updated == {}
        assert report.errors == {}
        assert fake.created_db_pages == 4
        assert sync_count(session) == 4
        goal_pages = [
            p for p in fake.db_pages.values() if p["database_id"] == fake.databases["Goals"]
        ]
        assert len(goal_pages) == 1
        title = goal_pages[0]["properties"]["Title"]["title"][0]["text"]["content"]
        assert title == "Harden CI pipeline"

    def test_rerun_updates_instead_of_creating(self, session):
        seed_entities(session)
        fake = FakeNotion()
        sync_all(session, make_client(fake), PARENT_ID)
        pages_after_first = fake.created_db_pages

        report = sync_all(session, make_client(fake), PARENT_ID)

        assert fake.created_db_pages == pages_after_first
        assert report.created == {}
        assert report.updated == {"repository": 1, "goal": 1, "task": 1, "run": 1}
        assert fake.updated_db_pages == 4
        assert sync_count(session) == 4

    def test_one_entity_failing_does_not_abort_the_rest(self, session):
        seed_entities(session)
        fake = FakeNotion()
        # Bootstrap first so the Goals database id is known, then arm the failure.
        bootstrap_workspace(make_client(fake), PARENT_ID)
        fake.fail_page_creates[fake.databases["Goals"]] = 1

        report = sync_all(session, make_client(fake), PARENT_ID)

        assert list(report.errors) == ["goal"]
        assert len(report.errors["goal"]) == 1
        assert report.created == {"repository": 1, "task": 1, "run": 1}
        assert sync_count(session) == 3

    def test_rerun_after_partial_failure_heals(self, session):
        seed_entities(session)
        fake = FakeNotion()
        bootstrap_workspace(make_client(fake), PARENT_ID)
        fake.fail_page_creates[fake.databases["Goals"]] = 1
        sync_all(session, make_client(fake), PARENT_ID)

        report = sync_all(session, make_client(fake), PARENT_ID)

        assert report.errors == {}
        assert report.created == {"goal": 1}
        assert report.updated == {"repository": 1, "task": 1, "run": 1}
        assert sync_count(session) == 4
        goal_pages = [
            p for p in fake.db_pages.values() if p["database_id"] == fake.databases["Goals"]
        ]
        assert len(goal_pages) == 1

    def test_secrets_are_redacted_before_reaching_notion(self, session):
        secret = "ghp_" + "a" * 36
        seed_entities(session, exit_summary=f"Done. Token used: {secret}")
        fake = FakeNotion()

        report = sync_all(session, make_client(fake), PARENT_ID)

        assert report.errors == {}
        payload = json.dumps(fake.db_pages)
        assert "ghp_" not in payload
        assert "[REDACTED]" in payload
