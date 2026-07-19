import pytest
from fastapi.testclient import TestClient

from nexus.api.app import app

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db_session):
    """Client that behaves like the dashboard/CLI (sends the local-owner header)."""
    return TestClient(app, base_url="http://localhost", headers={"X-Nexus-Client": "tests"})


@pytest.fixture
def hostile_client(db_session):
    """Client with no X-Nexus-Client header (simulates a browser form POST)."""
    return TestClient(app, base_url="http://localhost")


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

        plan = client.get(f"/api/goals/{goal['id']}/plan").json()
        assert plan["planner"] == "deterministic"
        assert plan["objective"]

        listing = client.get("/api/goals").json()
        assert any(item["id"] == goal["id"] for item in listing["items"])

        cancelled = client.post(f"/api/goals/{goal['id']}/cancel").json()
        assert cancelled["ok"]
        after = client.get(f"/api/goals/{goal['id']}").json()
        assert after["status"] == "cancelled"
        assert all(task["status"] == "cancelled" for task in after["tasks"])

    def test_unregistered_repository_rejected(self, client):
        response = client.post(
            "/api/goals",
            json={
                "title": "Repo goal",
                "description": "work in a repo",
                "repository": "never/registered",
            },
        )
        assert response.status_code == 422
        assert "not registered" in response.json()["detail"]

    def test_task_detail_and_settings(self, client):
        goal = client.post(
            "/api/goals",
            json={
                "title": "Detail goal",
                "description": "do a detailed thing",
                "requested_worker": "fake",
            },
        ).json()
        task_id = client.get(f"/api/goals/{goal['id']}").json()["tasks"][0]["id"]
        detail = client.get(f"/api/tasks/{task_id}").json()
        assert detail["instruction"]
        assert detail["validation_results"] == []
        settings = client.get("/api/settings").json()
        assert settings["cost_mode"]["paid_apis_enabled"] is False
        assert settings["review_policy"] in {"required", "preferred", "disabled"}

    def test_validation_rejects_bad_input(self, client):
        response = client.post("/api/goals", json={"title": "x", "description": ""})
        assert response.status_code == 422

    def test_unknown_goal_404(self, client):
        assert client.get("/api/goals/goal_missing").status_code == 404

    def test_security_headers_present(self, client):
        response = client.get("/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"


class TestLocalOwnerProtection:
    def test_state_change_without_client_header_refused(self, hostile_client):
        response = hostile_client.post(
            "/api/goals", json={"title": "attack", "description": "attack"}
        )
        assert response.status_code == 403
        assert "X-Nexus-Client" in response.json()["detail"]

    def test_reads_allowed_without_header(self, hostile_client):
        assert hostile_client.get("/health").status_code == 200

    def test_foreign_host_refused(self, db_session):
        client = TestClient(app, headers={"Host": "evil.example.com", "X-Nexus-Client": "x"})
        response = client.get("/health")
        assert response.status_code == 421

    def test_approval_decision_resumes_blocked_task(self, client, db_session):
        """The API decision path goes through the approvals service."""
        from sqlalchemy import select

        from nexus.db.models import Approval, Goal, Task
        from nexus.domain.enums import TaskStatus

        goal = Goal(title="g", description="d")
        db_session.add(goal)
        db_session.flush()
        task = Task(goal_id=goal.id, title="t", instruction="i", status=TaskStatus.BLOCKED)
        db_session.add(task)
        db_session.flush()
        approval = Approval(
            kind="run-untrusted-repository-scripts",
            description="test",
            goal_id=goal.id,
            task_id=task.id,
        )
        db_session.add(approval)
        db_session.commit()

        response = client.post(
            f"/api/approvals/{approval.id}/decision", json={"decision": "approved"}
        )
        assert response.json()["ok"]
        db_session.expire_all()
        refreshed = db_session.scalars(select(Task).where(Task.id == task.id)).one()
        assert refreshed.status == TaskStatus.READY
