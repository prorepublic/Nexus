import json
from typing import Any

import httpx
import pytest

from nexus.adapters.notion import (
    DATABASES,
    WORKSPACE_PAGES,
    NotionClient,
    NotionError,
    NotionNotConfigured,
    bootstrap_workspace,
    normalize_page_id,
)

PARENT_ID = "11112222333344445555666677778888"


class FakeNotion:
    """In-memory Notion: pages/databases keyed by parent, title-addressable."""

    def __init__(self) -> None:
        self.children: dict[str, list[dict[str, Any]]] = {}
        self.created_pages = 0
        self.created_databases = 0
        self.rate_limit_first_n = 0
        self._calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self._calls += 1
        if self.rate_limit_first_n and self._calls <= self.rate_limit_first_n:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        path = request.url.path
        if path.startswith("/v1/blocks/") and path.endswith("/children"):
            parent = path.split("/")[3]
            return httpx.Response(
                200,
                json={
                    "results": self.children.get(parent, []),
                    "has_more": False,
                },
            )
        if path == "/v1/pages":
            body = json.loads(request.content)
            parent = body["parent"]["page_id"]
            title = body["properties"]["title"]["title"][0]["text"]["content"]
            page_id = f"page-{self.created_pages}"
            self.created_pages += 1
            self.children.setdefault(parent, []).append(
                {"id": page_id, "type": "child_page", "child_page": {"title": title}}
            )
            return httpx.Response(200, json={"id": page_id})
        if path == "/v1/databases":
            body = json.loads(request.content)
            parent = body["parent"]["page_id"]
            title = body["title"][0]["text"]["content"]
            db_id = f"db-{self.created_databases}"
            self.created_databases += 1
            self.children.setdefault(parent, []).append(
                {"id": db_id, "type": "child_database", "child_database": {"title": title}}
            )
            return httpx.Response(200, json={"id": db_id})
        return httpx.Response(404, json={"message": "not found"})


def make_client(fake: FakeNotion) -> NotionClient:
    return NotionClient(
        token="ntn_test_token_not_real_aaaaaaaaaaaa", transport=httpx.MockTransport(fake.handler)
    )


class TestNormalizePageId:
    def test_bare_id(self):
        assert normalize_page_id(PARENT_ID).replace("-", "") == PARENT_ID

    def test_url_with_slug(self):
        url = f"https://www.notion.so/My-Page-{PARENT_ID}"
        assert normalize_page_id(url).replace("-", "") == PARENT_ID

    def test_invalid(self):
        with pytest.raises(NotionError):
            normalize_page_id("not-a-page")


class TestBootstrap:
    def test_creates_full_workspace(self):
        fake = FakeNotion()
        report = bootstrap_workspace(make_client(fake), PARENT_ID)
        assert "Nexus Home" in report.created_pages
        assert set(WORKSPACE_PAGES) <= set(report.created_pages)
        assert set(DATABASES) == set(report.created_databases)

    def test_rerun_is_idempotent(self):
        fake = FakeNotion()
        bootstrap_workspace(make_client(fake), PARENT_ID)
        pages_after_first = fake.created_pages
        dbs_after_first = fake.created_databases

        report = bootstrap_workspace(make_client(fake), PARENT_ID)
        assert fake.created_pages == pages_after_first
        assert fake.created_databases == dbs_after_first
        assert report.created_pages == []
        assert report.created_databases == []
        assert "Nexus Home" in report.existing_pages

    def test_partial_workspace_reconciled(self):
        fake = FakeNotion()
        client = make_client(fake)
        home_id = client.create_page(normalize_page_id(PARENT_ID), "Nexus Home")
        client.create_page(home_id, "Vision")
        report = bootstrap_workspace(make_client(fake), PARENT_ID)
        assert "Vision" in report.existing_pages
        assert "Roadmap" in report.created_pages

    def test_rate_limit_retried(self):
        fake = FakeNotion()
        fake.rate_limit_first_n = 2
        report = bootstrap_workspace(make_client(fake), PARENT_ID)
        assert report.home_page_id is not None

    def test_missing_token_raises_not_configured(self, monkeypatch):
        monkeypatch.delenv("NEXUS_NOTION_TOKEN", raising=False)
        from nexus.config import get_settings

        get_settings.cache_clear()
        with pytest.raises(NotionNotConfigured):
            NotionClient(token=None)
