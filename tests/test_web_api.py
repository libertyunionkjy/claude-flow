"""Web API endpoints tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.task_manager import TaskManager


@pytest.fixture
def web_app(cf_project):
    """Create a Flask test client with a real TaskManager."""
    from claude_flow.web.app import create_app

    cfg = Config.load(cf_project)
    app = create_app(cf_project, cfg)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(web_app):
    """Flask test client."""
    return web_app.test_client()


@pytest.fixture
def tm(web_app) -> TaskManager:
    """Get the TaskManager from the app config."""
    return web_app.config["TASK_MANAGER"]


# -- Basic task CRUD ----------------------------------------------------------


class TestTaskCRUD:
    def test_list_tasks_empty(self, client):
        resp = client.get("/api/tasks")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"] == []

    def test_create_task(self, client):
        resp = client.post(
            "/api/tasks",
            json={"title": "Test", "prompt": "Do something", "priority": 1},
        )
        data = resp.get_json()
        assert resp.status_code == 201
        assert data["ok"] is True
        assert data["data"]["title"] == "Test"
        assert data["data"]["status"] == "pending"
        assert data["data"]["priority"] == 1

    def test_create_task_missing_fields(self, client):
        resp = client.post("/api/tasks", json={"title": "Test"})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["ok"] is False

    def test_get_task(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.get(f"/api/tasks/{task.id}")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["id"] == task.id

    def test_get_task_not_found(self, client):
        resp = client.get("/api/tasks/nonexistent")
        data = resp.get_json()
        assert data["ok"] is False

    def test_delete_task(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.delete(f"/api/tasks/{task.id}")
        data = resp.get_json()
        assert data["ok"] is True
        assert tm.get(task.id) is None

    def test_update_task_status(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.patch(
            f"/api/tasks/{task.id}",
            json={"status": "approved"},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "approved"

    def test_update_task_priority(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.patch(
            f"/api/tasks/{task.id}",
            json={"priority": 5},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["priority"] == 5


# -- Approve / Chat ----------------------------------------------------------


class TestApproveChat:
    def test_approve_task(self, client, tm, web_app):
        task = tm.add("T1", "P1")
        tm.update_status(task.id, TaskStatus.PLANNED)
        resp = client.post(f"/api/tasks/{task.id}/approve")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "approved"

    def test_approve_wrong_status(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.post(f"/api/tasks/{task.id}/approve")
        data = resp.get_json()
        assert data["ok"] is False

    def test_chat_get_empty(self, client, tm):
        """GET /chat returns empty when no session exists."""
        task = tm.add("T1", "P1")
        resp = client.get(f"/api/tasks/{task.id}/chat")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["exists"] is False

    def test_chat_send_creates_session(self, client, tm, web_app):
        """POST /chat creates a session and returns accepted (async mode)."""
        task = tm.add("T1", "P1")

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("AI response here", "")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("claude_flow.chat.subprocess.Popen", return_value=mock_proc):
            resp = client.post(
                f"/api/tasks/{task.id}/chat",
                json={"message": "How should we implement this?"},
            )
            data = resp.get_json()
            assert data["ok"] is True
            assert data["data"]["accepted"] is True
            assert data["data"]["thinking"] is True

            # Wait for background thread to complete
            import time
            time.sleep(0.5)

        # Verify the AI response arrived via GET
        resp = client.get(f"/api/tasks/{task.id}/chat")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["thinking"] is False
        assert len(data["data"]["messages"]) == 2  # user + assistant
        assert data["data"]["messages"][1]["content"] == "AI response here"

    def test_chat_get_with_history(self, client, tm, web_app):
        """GET /chat returns messages and thinking status after a send."""
        task = tm.add("T1", "P1")

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("Sure, here is my suggestion", "")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0

        with patch("claude_flow.chat.subprocess.Popen", return_value=mock_proc):
            client.post(
                f"/api/tasks/{task.id}/chat",
                json={"message": "Hello"},
            )

            # Wait for background thread to complete
            import time
            time.sleep(0.5)

        resp = client.get(f"/api/tasks/{task.id}/chat")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["exists"] is True
        assert data["data"]["thinking"] is False
        assert len(data["data"]["messages"]) == 2

    def test_chat_send_empty_message(self, client, tm):
        """POST /chat requires non-empty message."""
        task = tm.add("T1", "P1")
        resp = client.post(
            f"/api/tasks/{task.id}/chat",
            json={"message": ""},
        )
        data = resp.get_json()
        assert data["ok"] is False

    def test_chat_finalize(self, client, tm, web_app):
        """POST /chat/finalize triggers plan generation from chat."""
        import time
        import threading
        task = tm.add("T1", "P1")

        # First create a chat session with messages
        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="AI plan idea", stderr=""
            )
            client.post(
                f"/api/tasks/{task.id}/chat",
                json={"message": "Plan this feature"},
            )
            # Wait for async response to complete
            time.sleep(0.5)

        # Block the background thread so it doesn't finish before our assertion
        barrier = threading.Event()

        def _blocking_generate(*args, **kwargs):
            barrier.wait(timeout=5)
            return "fake-plan.md"

        # Then finalize
        with patch.object(
            web_app.config["PLANNER"], "generate_from_chat",
            side_effect=_blocking_generate,
        ):
            resp = client.post(f"/api/tasks/{task.id}/chat/finalize")
            data = resp.get_json()
            assert data["ok"] is True
            assert data["data"]["status"] == "planning"
            barrier.set()  # Unblock background thread

    def test_chat_finalize_no_session(self, client, tm):
        """Finalize fails if no chat session exists."""
        task = tm.add("T1", "P1")
        resp = client.post(f"/api/tasks/{task.id}/chat/finalize")
        data = resp.get_json()
        assert data["ok"] is False

    def test_approve_all(self, client, tm, web_app):
        t1 = tm.add("T1", "P1")
        t2 = tm.add("T2", "P2")
        tm.update_status(t1.id, TaskStatus.PLANNED)
        tm.update_status(t2.id, TaskStatus.PLANNED)
        resp = client.post("/api/approve-all")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["approved"] == 2


# -- Plan generation ---------------------------------------------------------


class TestPlanAPI:
    def test_plan_task_wrong_status(self, client, tm):
        task = tm.add("T1", "P1")
        tm.update_status(task.id, TaskStatus.APPROVED)
        resp = client.post(f"/api/tasks/{task.id}/plan")
        data = resp.get_json()
        assert data["ok"] is False

    def test_plan_task_starts_planning(self, client, tm):
        """Plan task should set status to planning and return ok."""
        import threading
        task = tm.add("T1", "P1")

        # Block the background thread so it doesn't finish before our assertion
        barrier = threading.Event()

        def _blocking_generate(*args, **kwargs):
            barrier.wait(timeout=5)
            return "fake-plan.md"

        with patch.object(
            client.application.config["PLANNER"], "generate",
            side_effect=_blocking_generate,
        ):
            resp = client.post(f"/api/tasks/{task.id}/plan")
            data = resp.get_json()
            assert data["ok"] is True
            # Status should transition to planning
            updated = tm.get(task.id)
            assert updated.status == TaskStatus.PLANNING
            barrier.set()  # Unblock background thread

    def test_get_plan_not_found(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.get(f"/api/tasks/{task.id}/plan")
        data = resp.get_json()
        assert data["ok"] is False

    def test_get_plan_content(self, client, tm, cf_project):
        task = tm.add("T1", "P1")
        plans_dir = cf_project / ".claude-flow" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plans_dir / f"{task.id}.md"
        plan_file.write_text("# Plan\n1. Step one\n2. Step two")
        resp = client.get(f"/api/tasks/{task.id}/plan")
        data = resp.get_json()
        assert data["ok"] is True
        assert "Step one" in data["data"]["content"]

    def test_plan_all(self, client, tm):
        t1 = tm.add("T1", "P1")
        t2 = tm.add("T2", "P2")

        with patch.object(
            client.application.config["PLANNER"], "generate", return_value=None
        ):
            resp = client.post("/api/plan-all")
            data = resp.get_json()
            assert data["ok"] is True
            assert data["data"]["planned"] == 2


# -- Reset -------------------------------------------------------------------


class TestReset:
    def test_reset_failed_task(self, client, tm):
        task = tm.add("T1", "P1")
        tm.update_status(task.id, TaskStatus.FAILED, "Error")
        resp = client.post(f"/api/tasks/{task.id}/reset")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "pending"

    def test_reset_needs_input_task(self, client, tm):
        task = tm.add("T1", "P1")
        tm.update_status(task.id, TaskStatus.NEEDS_INPUT)
        resp = client.post(f"/api/tasks/{task.id}/reset")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "pending"

    def test_reset_wrong_status(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.post(f"/api/tasks/{task.id}/reset")
        data = resp.get_json()
        assert data["ok"] is False


# -- Log ---------------------------------------------------------------------


class TestLog:
    def test_get_log_not_found(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.get(f"/api/tasks/{task.id}/log")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["exists"] is False

    def test_get_log_content(self, client, tm, cf_project):
        task = tm.add("T1", "P1")
        logs_dir = cf_project / ".claude-flow" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / f"{task.id}.log"
        log_file.write_text("some log output here")
        resp = client.get(f"/api/tasks/{task.id}/log")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["exists"] is True
        assert "some log output" in data["data"]["content"]


# -- Respond -----------------------------------------------------------------


class TestRespond:
    def test_respond_task(self, client, tm):
        task = tm.add("T1", "P1")
        tm.update_status(task.id, TaskStatus.NEEDS_INPUT)
        resp = client.post(
            f"/api/tasks/{task.id}/respond",
            json={"message": "Here is more info"},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "approved"

    def test_respond_wrong_status(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.post(
            f"/api/tasks/{task.id}/respond",
            json={"message": "Info"},
        )
        data = resp.get_json()
        assert data["ok"] is False

    def test_respond_empty_message(self, client, tm):
        task = tm.add("T1", "P1")
        tm.update_status(task.id, TaskStatus.NEEDS_INPUT)
        resp = client.post(
            f"/api/tasks/{task.id}/respond",
            json={"message": ""},
        )
        data = resp.get_json()
        assert data["ok"] is False


# -- Retry all ---------------------------------------------------------------


class TestRetryAll:
    def test_retry_all(self, client, tm):
        t1 = tm.add("T1", "P1")
        t2 = tm.add("T2", "P2")
        tm.update_status(t1.id, TaskStatus.FAILED)
        tm.update_status(t2.id, TaskStatus.FAILED)
        resp = client.post("/api/retry-all")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["retried"] == 2
        assert tm.get(t1.id).status == TaskStatus.APPROVED
        assert tm.get(t2.id).status == TaskStatus.APPROVED

    def test_retry_all_none_failed(self, client):
        resp = client.post("/api/retry-all")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["retried"] == 0


# -- Status / Workers --------------------------------------------------------


class TestStatusWorkers:
    def test_global_status(self, client, tm):
        tm.add("T1", "P1")
        tm.add("T2", "P2")
        resp = client.get("/api/status")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["total"] == 2
        assert data["data"]["counts"]["pending"] == 2

    def test_workers(self, client):
        resp = client.get("/api/workers")
        data = resp.get_json()
        assert data["ok"] is True


# -- Run (basic validation only, no actual worker execution) ------------------


class TestRunAPI:
    def test_run_task_wrong_status(self, client, tm):
        task = tm.add("T1", "P1")
        resp = client.post(f"/api/tasks/{task.id}/run")
        data = resp.get_json()
        assert data["ok"] is False

    def test_run_all_no_approved(self, client):
        resp = client.post("/api/run", json={"num_workers": 1})
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["started"] == 0


class TestRecoverStuckPlanningTasks:
    """Tests for _recover_stuck_planning_tasks startup recovery."""

    def test_recovers_finalized_session_without_plan(self, cf_project):
        """A task in PLANNING with finalized chat but no plan file
        should have its chat session reset to active on app startup."""
        from claude_flow.chat import ChatManager
        from claude_flow.web.app import _recover_stuck_planning_tasks

        cfg = Config.load(cf_project)
        tm = TaskManager(cf_project)
        chat_mgr = ChatManager(cf_project, cfg)
        plans_dir = cf_project / ".claude-flow" / "plans"

        # Create a task in PLANNING state
        task = tm.add(title="Stuck task", prompt="Do something")
        tm.update_status(task.id, TaskStatus.PLANNING)

        # Create and finalize a chat session (simulating interrupted finalize)
        chat_mgr.create_session(task.id)
        chat_mgr.add_message(task.id, "user", "hello")
        chat_mgr.finalize(task.id)

        # Verify precondition: session is finalized, no plan file
        session = chat_mgr.get_session(task.id)
        assert session.status == "finalized"
        assert not (plans_dir / f"{task.id}.md").exists()

        # Run recovery
        _recover_stuck_planning_tasks(tm, chat_mgr, plans_dir)

        # Session should be reset to active
        recovered = chat_mgr.get_session(task.id)
        assert recovered.status == "active"
        assert recovered.thinking is False

    def test_does_not_reset_if_plan_exists(self, cf_project):
        """A finalized chat session with an existing plan file should
        not be reset (plan was generated successfully)."""
        from claude_flow.chat import ChatManager
        from claude_flow.web.app import _recover_stuck_planning_tasks

        cfg = Config.load(cf_project)
        tm = TaskManager(cf_project)
        chat_mgr = ChatManager(cf_project, cfg)
        plans_dir = cf_project / ".claude-flow" / "plans"

        task = tm.add(title="Complete task", prompt="Done")
        tm.update_status(task.id, TaskStatus.PLANNING)

        chat_mgr.create_session(task.id)
        chat_mgr.finalize(task.id)

        # Create plan file (simulating successful generate_from_chat)
        plans_dir.mkdir(parents=True, exist_ok=True)
        (plans_dir / f"{task.id}.md").write_text("# Plan")

        _recover_stuck_planning_tasks(tm, chat_mgr, plans_dir)

        # Session should remain finalized
        session = chat_mgr.get_session(task.id)
        assert session.status == "finalized"
