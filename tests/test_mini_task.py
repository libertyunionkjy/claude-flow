"""Tests for mini task functionality.

Mini tasks skip the planning/approval workflow and go directly to APPROVED status.
"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from claude_flow.cli import main
from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus, TaskType
from claude_flow.task_manager import TaskManager


class TestMiniTaskModel:
    """Test TaskType enum and mini task model fields."""

    def test_task_type_values(self):
        assert TaskType.NORMAL.value == "normal"
        assert TaskType.MINI.value == "mini"

    def test_default_task_type_is_normal(self):
        task = Task(title="Normal task", prompt="Do something")
        assert task.task_type == TaskType.NORMAL
        assert not task.is_mini

    def test_mini_task_is_mini(self):
        task = Task(
            title="Mini task", prompt="Do something",
            task_type=TaskType.MINI,
        )
        assert task.task_type == TaskType.MINI
        assert task.is_mini

    def test_task_type_serialization(self):
        task = Task(
            title="Test", prompt="Prompt",
            task_type=TaskType.MINI,
        )
        d = task.to_dict()
        assert d["task_type"] == "mini"

    def test_task_type_deserialization(self):
        d = {
            "id": "task-001",
            "title": "Test",
            "prompt": "Prompt",
            "status": "approved",
            "task_type": "mini",
            "created_at": "2026-03-10T10:00:00",
        }
        task = Task.from_dict(d)
        assert task.task_type == TaskType.MINI
        assert task.is_mini

    def test_missing_task_type_defaults_to_normal(self):
        """Backward compatibility: old tasks without task_type field."""
        d = {
            "id": "task-001",
            "title": "Old task",
            "prompt": "Prompt",
            "status": "pending",
            "created_at": "2026-03-10T10:00:00",
        }
        task = Task.from_dict(d)
        assert task.task_type == TaskType.NORMAL
        assert not task.is_mini

    def test_roundtrip_mini_task(self):
        task = Task(
            title="Mini roundtrip", prompt="Test",
            task_type=TaskType.MINI, status=TaskStatus.APPROVED,
        )
        d = task.to_dict()
        restored = Task.from_dict(d)
        assert restored.task_type == TaskType.MINI
        assert restored.is_mini
        assert restored.status == TaskStatus.APPROVED


class TestMiniTaskManager:
    """Test TaskManager.add_mini method."""

    def _make_manager(self, tmp_path: Path) -> TaskManager:
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        return TaskManager(tmp_path)

    def test_add_mini_creates_approved_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add_mini("Run tests", "run pytest -v")
        assert task.status == TaskStatus.APPROVED
        assert task.task_type == TaskType.MINI
        assert task.is_mini
        assert task.title == "Run tests"
        assert task.prompt == "run pytest -v"

    def test_add_mini_with_priority(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add_mini("High priority", "do something", priority=5)
        assert task.priority == 5

    def test_mini_task_claimable_immediately(self, tmp_path):
        """Mini tasks should be immediately claimable by workers."""
        mgr = self._make_manager(tmp_path)
        mgr.add_mini("Quick task", "do something fast")
        claimed = mgr.claim_next(worker_id=0)
        assert claimed is not None
        assert claimed.is_mini
        assert claimed.status == TaskStatus.RUNNING

    def test_mini_task_persistence(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add_mini("Persist mini", "test persistence")
        mgr2 = TaskManager(tmp_path)
        loaded = mgr2.get(task.id)
        assert loaded is not None
        assert loaded.task_type == TaskType.MINI
        assert loaded.status == TaskStatus.APPROVED

    def test_normal_and_mini_coexist(self, tmp_path):
        """Normal and mini tasks should coexist in the same task list."""
        mgr = self._make_manager(tmp_path)
        normal = mgr.add("Normal", "normal prompt")
        mini = mgr.add_mini("Mini", "mini prompt")
        tasks = mgr.list_tasks()
        assert len(tasks) == 2
        assert not tasks[0].is_mini  # normal task
        assert tasks[1].is_mini  # mini task

    def test_only_mini_task_claimed_when_normal_pending(self, tmp_path):
        """Only approved tasks are claimable; normal pending tasks are not."""
        mgr = self._make_manager(tmp_path)
        mgr.add("Normal pending", "pending prompt")
        mgr.add_mini("Mini approved", "mini prompt")
        claimed = mgr.claim_next(worker_id=0)
        assert claimed is not None
        assert claimed.is_mini
        # No more claimable tasks
        assert mgr.claim_next(worker_id=1) is None


class TestMiniTaskCLI:
    """Test CLI commands for mini tasks."""

    def test_task_mini_add(self, git_repo):
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(
            main, ["task", "mini", "run pytest -v"],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0
        assert "Mini task added" in result.output
        assert "approved" in result.output

    def test_task_mini_with_title(self, git_repo):
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(
            main, ["task", "mini", "-t", "Run Tests", "run pytest -v"],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0
        assert "Run Tests" in result.output

    def test_task_mini_auto_title_truncation(self, git_repo):
        """Long prompts get truncated in the auto-generated title."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        runner = CliRunner()
        long_prompt = "x" * 100
        result = runner.invoke(
            main, ["task", "mini", long_prompt],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0
        assert "..." in result.output

    def test_task_list_shows_mini_tag(self, git_repo):
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        tm.add("Normal task", "normal prompt")
        tm.add_mini("Mini task", "mini prompt")
        runner = CliRunner()
        result = runner.invoke(
            main, ["task", "list"],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0
        assert "[mini]" in result.output
        # Normal task should not have [mini] tag
        lines = result.output.strip().splitlines()
        normal_line = [l for l in lines if "Normal task" in l][0]
        assert "[mini]" not in normal_line

    def test_plan_skips_mini_task(self, git_repo):
        """cf plan should not try to plan mini tasks."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        tm.add_mini("Mini task", "mini prompt")
        runner = CliRunner()
        result = runner.invoke(
            main, ["plan"],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0
        assert "No pending tasks" in result.output

    def test_plan_specific_mini_task_rejected(self, git_repo):
        """cf plan -t <mini_task_id> should reject with helpful message."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        mini = tm.add_mini("Mini task", "mini prompt")
        runner = CliRunner()
        result = runner.invoke(
            main, ["plan", "-F", "-t", mini.id],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0
        assert "mini task" in result.output.lower()
        assert "no planning needed" in result.output.lower()

    def test_plan_interactive_mini_task_rejected(self, git_repo):
        """cf plan -i -t <mini_task_id> should reject."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        mini = tm.add_mini("Mini task", "mini prompt")
        runner = CliRunner()
        result = runner.invoke(
            main, ["plan", "-i", "-t", mini.id],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0
        assert "mini task" in result.output.lower()

    def test_reset_mini_task_goes_to_approved(self, git_repo):
        """Reset a failed mini task should go back to approved, not pending."""
        (git_repo / ".claude-flow").mkdir(exist_ok=True)
        tm = TaskManager(git_repo)
        mini = tm.add_mini("Mini task", "mini prompt")
        tm.update_status(mini.id, TaskStatus.FAILED, "some error")
        runner = CliRunner()
        result = runner.invoke(
            main, ["reset", mini.id],
            catch_exceptions=False,
            env={"CF_PROJECT_ROOT": str(git_repo)},
        )
        assert result.exit_code == 0
        assert "approved" in result.output
        updated = tm.get(mini.id)
        assert updated.status == TaskStatus.APPROVED

    def test_mini_task_with_run_flag(self, git_repo):
        """cf task mini --run should create and immediately execute."""
        (git_repo / ".claude-flow" / "logs").mkdir(parents=True, exist_ok=True)
        runner = CliRunner()

        with patch("claude_flow.cli.Worker") as MockWorker:
            mock_worker_instance = MagicMock()
            mock_worker_instance.execute_task.return_value = True
            MockWorker.return_value = mock_worker_instance

            result = runner.invoke(
                main, ["task", "mini", "--run", "run some script"],
                catch_exceptions=False,
                env={"CF_PROJECT_ROOT": str(git_repo)},
            )

        assert result.exit_code == 0
        assert "Mini task added" in result.output
        assert "Executing mini task" in result.output


class TestMiniTaskWebAPI:
    """Test web API support for mini tasks."""

    def _create_app(self, git_repo):
        """Create test Flask app."""
        (git_repo / ".claude-flow" / "plans").mkdir(parents=True, exist_ok=True)
        (git_repo / ".claude-flow" / "chats").mkdir(parents=True, exist_ok=True)
        (git_repo / ".claude-flow" / "logs").mkdir(parents=True, exist_ok=True)
        cfg = Config()
        from claude_flow.web import create_app
        return create_app(git_repo, cfg)

    def test_create_mini_task_via_api(self, git_repo):
        app = self._create_app(git_repo)
        with app.test_client() as client:
            resp = client.post("/api/tasks", json={
                "title": "Mini API task",
                "prompt": "do something quick",
                "task_type": "mini",
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["ok"] is True
            assert data["data"]["task_type"] == "mini"
            assert data["data"]["status"] == "approved"

    def test_create_normal_task_via_api(self, git_repo):
        """Default task_type should be normal."""
        app = self._create_app(git_repo)
        with app.test_client() as client:
            resp = client.post("/api/tasks", json={
                "title": "Normal API task",
                "prompt": "do something",
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["data"]["task_type"] == "normal"
            assert data["data"]["status"] == "pending"

    def test_plan_mini_task_rejected_via_api(self, git_repo):
        """POST /api/tasks/<id>/plan should reject mini tasks."""
        app = self._create_app(git_repo)
        with app.test_client() as client:
            # Create mini task
            resp = client.post("/api/tasks", json={
                "title": "Mini", "prompt": "quick", "task_type": "mini",
            })
            task_id = resp.get_json()["data"]["id"]

            # Try to plan it
            resp = client.post(f"/api/tasks/{task_id}/plan")
            assert resp.status_code == 400
            data = resp.get_json()
            assert "mini task" in data["error"].lower()

    def test_reset_mini_task_to_approved_via_api(self, git_repo):
        """Reset a failed mini task via API should go to approved."""
        app = self._create_app(git_repo)
        tm = app.config["TASK_MANAGER"]
        with app.test_client() as client:
            # Create and fail a mini task
            resp = client.post("/api/tasks", json={
                "title": "Mini", "prompt": "quick", "task_type": "mini",
            })
            task_id = resp.get_json()["data"]["id"]
            tm.update_status(task_id, TaskStatus.FAILED, "test error")

            # Reset it
            resp = client.post(f"/api/tasks/{task_id}/reset")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["data"]["status"] == "approved"

    def test_plan_all_excludes_mini_tasks(self, git_repo):
        """POST /api/plan-all should skip mini tasks."""
        app = self._create_app(git_repo)
        tm = app.config["TASK_MANAGER"]
        with app.test_client() as client:
            # Add only a mini task (already approved, not pending)
            tm.add_mini("Mini", "quick")

            resp = client.post("/api/plan-all")
            data = resp.get_json()
            assert data["data"]["planned"] == 0
