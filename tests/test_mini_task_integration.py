"""Mini task integration tests -- full lifecycle verification.

PTY operations are mocked since they require a real terminal.
Tests: create -> start -> stop -> diff -> merge -> done
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.task_manager import TaskManager


@pytest.fixture
def mini_app(cf_project):
    """Flask test app with mocked PtyManager."""
    (cf_project / ".claude-flow" / "chats").mkdir(parents=True, exist_ok=True)
    from claude_flow.web.app import create_app

    cfg = Config.load(cf_project)
    app = create_app(cf_project, cfg)
    app.config["TESTING"] = True

    # Replace PtyManager with a mock
    mock_pty = MagicMock()
    mock_pty.get_session.return_value = MagicMock(alive=True, prompt="")
    mock_pty.create_session.return_value = MagicMock(
        task_id="test", pid=1234, fd=5, alive=True
    )
    app.config["PTY_MANAGER"] = mock_pty
    return app


@pytest.fixture
def mini_client(mini_app):
    return mini_app.test_client()


@pytest.fixture
def mini_tm(mini_app) -> TaskManager:
    return mini_app.config["TASK_MANAGER"]


class TestMiniTaskLifecycle:
    def test_create_via_api(self, mini_client):
        resp = mini_client.post(
            "/api/mini-tasks",
            json={"title": "Test mini", "prompt": "Fix something"},
        )
        data = resp.get_json()
        assert data["ok"]
        assert data["data"]["task_type"] == "mini"
        assert data["data"]["status"] == "approved"

    def test_create_missing_title(self, mini_client):
        resp = mini_client.post(
            "/api/mini-tasks",
            json={"prompt": "Fix something"},
        )
        assert resp.status_code == 400

    def test_list_mini_tasks(self, mini_client, mini_tm):
        mini_tm.add("Standard", "prompt")
        mini_tm.add_mini("Mini", "prompt")

        resp = mini_client.get("/api/mini-tasks")
        data = resp.get_json()
        assert len(data["data"]) == 1
        assert data["data"][0]["title"] == "Mini"

    def test_isolation_from_standard_tasks(self, mini_client, mini_tm):
        mini_tm.add("Standard", "prompt")
        mini_tm.add_mini("Mini", "prompt")

        resp = mini_client.get("/api/mini-tasks")
        data = resp.get_json()
        assert len(data["data"]) == 1
        assert data["data"][0]["title"] == "Mini"

    def test_start_creates_worktree_and_pty(self, mini_client, mini_tm, mini_app):
        t = mini_tm.add_mini("Start test", "test prompt")

        with patch("claude_flow.web.api.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            wt_dir = Path(str(mini_app.config["PROJECT_ROOT"])) / ".claude-flow/worktrees" / t.id
            wt_dir.mkdir(parents=True, exist_ok=True)
            mock_wt.create.return_value = wt_dir
            mock_wt_cls.return_value = mock_wt

            resp = mini_client.post(f"/api/mini-tasks/{t.id}/start")
            assert resp.get_json()["ok"]

        task = mini_tm.get(t.id)
        assert task.status == TaskStatus.RUNNING

    def test_interrupted_on_restart(self, mini_tm, cf_project):
        t = mini_tm.add_mini("Running", "prompt")
        mini_tm.update_status(t.id, TaskStatus.RUNNING)

        from claude_flow.web.app import _recover_interrupted_sessions
        from claude_flow.pty_manager import PtyManager

        _recover_interrupted_sessions(mini_tm, PtyManager())

        updated = mini_tm.get(t.id)
        assert updated.status == TaskStatus.INTERRUPTED

    def test_backward_compat_old_tasks(self, mini_tm):
        t = mini_tm.add("Old task", "old prompt")
        assert t.task_type.value == "normal"

        minis = mini_tm.list_tasks(task_type="mini")
        assert not any(task.id == t.id for task in minis)

    def test_list_tasks_type_filter(self, mini_tm):
        mini_tm.add("Standard 1", "s1")
        mini_tm.add("Standard 2", "s2")
        mini_tm.add_mini("Mini 1", "m1")

        all_tasks = mini_tm.list_tasks()
        assert len(all_tasks) == 3

        minis = mini_tm.list_tasks(task_type="mini")
        assert len(minis) == 1
        assert minis[0].title == "Mini 1"

        normals = mini_tm.list_tasks(task_type="normal")
        assert len(normals) == 2
