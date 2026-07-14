import pytest
from fastapi.testclient import TestClient

from nexus.api.app import app

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db_session):
    return TestClient(app)


class TestApi:
    def test_health(self, client):
        data = client.get("/health").json()
        assert data["status"] == "ok"
        assert data["database"] == "ok"

    def test_system_status_reports_cost_mode_disabled(self, client):
        data = client.get("/api/system/status").json()
        assert data["cost_mode"]["paid_apis_enabled"] is False
        names = {worker["name"] for worker in data["workers"]}
        assert {"fake", "claude-code", "codex-cli"} <= names

    def test_goal_lifecycle(self, client):
        created = client.post(
            "/api/goals",
            json={
                "title": "API test goal",
                "description": "Do something visible.",
                "acceptance_criteria": ["one", "two"],
                "requested_worker": "fake",
            },
        )
        assert created.status_code == 201
        goal = created.json()
        assert goal["status"] == "ready"

        detail = client.get(f"/api/goals/{goal['id']}").json()
        assert len(detail["tasks"]) == 3

        listing = client.get("/api/goals").json()
        assert any(item["id"] == goal["id"] for item in listing["items"])

        cancelled = client.post(f"/api/goals/{goal['id']}/cancel").json()
        assert cancelled["ok"]
        after = client.get(f"/api/goals/{goal['id']}").json()
        assert after["status"] == "cancelled"
        assert all(task["status"] == "cancelled" for task in after["tasks"])

    def test_validation_rejects_bad_input(self, client):
        response = client.post("/api/goals", json={"title": "x", "description": ""})
        assert response.status_code == 422

    def test_unknown_goal_404(self, client):
        assert client.get("/api/goals/goal_missing").status_code == 404

    def test_security_headers_present(self, client):
        response = client.get("/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
