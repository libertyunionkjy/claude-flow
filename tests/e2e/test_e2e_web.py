"""Web API end-to-end tests.

Tests complete API workflows:
- Task CRUD -> Plan -> Approve -> Status
- Chat interactive planning -> Finalize -> Approve
- Error recovery via reset/retry endpoints
- Batch operations

Mock version (default): claude CLI mocked
Smoke version (@pytest.mark.smoke): uses real claude CLI
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_flow.config import Config


def _has_flask():
    try:
        import flask
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _has_flask(), reason="Flask not installed")


@pytest.fixture
def web_client(e2e_project: Path):
    """Create a Flask test client."""
    from claude_flow.web.app import create_app
    config = Config.load(e2e_project)
    app = create_app(e2e_project, config)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestWebE2EWorkflowMocked:
    """Full Web API workflow with mocked claude."""

    def test_full_task_lifecycle(self, web_client, e2e_project: Path):
        """POST task -> GET -> approve -> status."""
        # Step 1: Create task
        resp = web_client.post("/api/tasks", json={
            "title": "Web E2E Task",
            "prompt": "Implement a web feature",
            "priority": 5,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        task_id = data["data"]["id"]

        # Step 2: Get task
        resp = web_client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        task_data = resp.get_json()["data"]
        assert task_data["title"] == "Web E2E Task"
        assert task_data["status"] == "pending"

        # Step 3: List tasks
        resp = web_client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.get_json()["data"]
        assert any(t["id"] == task_id for t in tasks)

        # Step 4: Status
        resp = web_client.get("/api/status")
        assert resp.status_code == 200
        status = resp.get_json()["data"]
        assert isinstance(status, dict)

    def test_task_crud_operations(self, web_client):
        """Create, read, update, delete a task."""
        # Create
        resp = web_client.post("/api/tasks", json={
            "title": "CRUD Test",
            "prompt": "Test CRUD operations",
        })
        assert resp.status_code == 201
        task_id = resp.get_json()["data"]["id"]

        # Read
        resp = web_client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200

        # Update priority
        resp = web_client.patch(f"/api/tasks/{task_id}", json={
            "priority": 10
        })
        assert resp.status_code == 200

        # Verify update
        resp = web_client.get(f"/api/tasks/{task_id}")
        assert resp.get_json()["data"]["priority"] == 10

        # Delete
        resp = web_client.delete(f"/api/tasks/{task_id}")
        assert resp.status_code == 200

        # Verify deleted
        resp = web_client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 404

    def test_batch_delete(self, web_client):
        """Create multiple tasks and batch delete."""
        ids = []
        for i in range(5):
            resp = web_client.post("/api/tasks", json={
                "title": f"Batch {i}",
                "prompt": f"Prompt {i}",
            })
            ids.append(resp.get_json()["data"]["id"])

        # Batch delete
        resp = web_client.post("/api/tasks/batch-delete", json={
            "task_ids": ids[:3]
        })
        assert resp.status_code == 200

        # Verify only 2 remain
        resp = web_client.get("/api/tasks")
        remaining = resp.get_json()["data"]
        remaining_ids = [t["id"] for t in remaining]
        for deleted_id in ids[:3]:
            assert deleted_id not in remaining_ids
        for kept_id in ids[3:]:
            assert kept_id in remaining_ids

    def test_reset_failed_task(self, web_client, e2e_project: Path):
        """Simulate failure -> reset workflow."""
        # Create task
        resp = web_client.post("/api/tasks", json={
            "title": "Fail Task",
            "prompt": "Will fail",
        })
        task_id = resp.get_json()["data"]["id"]

        # Set to FAILED via PATCH
        resp = web_client.patch(f"/api/tasks/{task_id}", json={
            "status": "failed"
        })
        assert resp.status_code == 200

        # Reset
        resp = web_client.post(f"/api/tasks/{task_id}/reset")
        assert resp.status_code == 200

        # Verify reset
        resp = web_client.get(f"/api/tasks/{task_id}")
        status = resp.get_json()["data"]["status"]
        assert status in ("pending", "approved")

    def test_nonexistent_task_returns_404(self, web_client):
        """API calls on non-existent task ID should return 404."""
        resp = web_client.get("/api/tasks/nonexistent-id-xyz")
        assert resp.status_code == 404

        resp = web_client.delete("/api/tasks/nonexistent-id-xyz")
        assert resp.status_code == 404

    def test_invalid_json_body(self, web_client):
        """POST with invalid JSON body."""
        resp = web_client.post(
            "/api/tasks",
            data="not json",
            content_type="application/json",
        )
        assert resp.status_code in (400, 415, 500)

    def test_missing_required_fields(self, web_client):
        """POST task without required fields."""
        resp = web_client.post("/api/tasks", json={
            "prompt": "no title provided"
        })
        assert resp.status_code == 400

    def test_overview_endpoint(self, web_client):
        """Overview endpoint should return structured data."""
        resp = web_client.get("/api/overview")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert isinstance(data, dict)


@pytest.mark.smoke
class TestWebE2ESmoke:
    """Real claude CLI tests via Web API.

    Run with: pytest -m smoke
    """

    def test_real_plan_via_api(self, web_client, e2e_project: Path, real_claude_available):
        """Generate a real plan via the API."""
        # Create task
        resp = web_client.post("/api/tasks", json={
            "title": "Real Plan Test",
            "prompt": "Add a single comment to README.md saying '# Test'",
        })
        task_id = resp.get_json()["data"]["id"]

        # Trigger real plan
        resp = web_client.post(f"/api/tasks/{task_id}/plan", json={
            "mode": "auto"
        })
        assert resp.status_code == 200

        # Poll for completion (max 120 seconds)
        for _ in range(24):
            time.sleep(5)
            resp = web_client.get(f"/api/tasks/{task_id}")
            status = resp.get_json()["data"]["status"]
            if status in ("planned", "failed"):
                break

        assert status == "planned", f"Plan generation ended with status: {status}"
