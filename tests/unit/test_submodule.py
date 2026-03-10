"""Tests for Git submodule support."""
from datetime import datetime
from claude_flow.models import Task, TaskStatus


class TestTaskSubmodules:
    def test_task_default_submodules_empty(self):
        task = Task(title="Test", prompt="prompt")
        assert task.submodules == []

    def test_task_with_submodules(self):
        task = Task(title="Test", prompt="prompt", submodules=["libs/core", "libs/ui"])
        assert task.submodules == ["libs/core", "libs/ui"]

    def test_task_to_dict_includes_submodules(self):
        task = Task(title="Test", prompt="prompt", submodules=["libs/core"])
        d = task.to_dict()
        assert d["submodules"] == ["libs/core"]

    def test_task_to_dict_empty_submodules(self):
        task = Task(title="Test", prompt="prompt")
        d = task.to_dict()
        assert d["submodules"] == []

    def test_task_from_dict_with_submodules(self):
        d = {
            "id": "task-001", "title": "Test", "prompt": "prompt",
            "status": "pending", "created_at": datetime.now().isoformat(),
            "submodules": ["libs/core", "libs/ui"],
        }
        task = Task.from_dict(d)
        assert task.submodules == ["libs/core", "libs/ui"]

    def test_task_from_dict_without_submodules_backward_compat(self):
        d = {
            "id": "task-001", "title": "Test", "prompt": "prompt",
            "status": "pending", "created_at": datetime.now().isoformat(),
        }
        task = Task.from_dict(d)
        assert task.submodules == []

    def test_task_roundtrip_with_submodules(self):
        task = Task(title="Roundtrip", prompt="p", submodules=["a/b", "c/d"])
        restored = Task.from_dict(task.to_dict())
        assert restored.submodules == ["a/b", "c/d"]


from claude_flow.task_manager import TaskManager


class TestTaskManagerSubmodules:
    def test_add_with_submodules(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add("Test", "prompt", submodules=["libs/core"])
        assert task.submodules == ["libs/core"]
        loaded = tm.get(task.id)
        assert loaded.submodules == ["libs/core"]

    def test_add_mini_with_submodules(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add_mini("Test", "prompt", submodules=["libs/ui"])
        assert task.submodules == ["libs/ui"]
        loaded = tm.get(task.id)
        assert loaded.submodules == ["libs/ui"]

    def test_add_without_submodules_default(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add("Test", "prompt")
        assert task.submodules == []


import subprocess
from pathlib import Path
import pytest
from claude_flow.worktree import WorktreeManager


class TestWorktreeSubmoduleInit:
    def test_create_with_submodule_initializes_submodule(self, git_repo_with_submodule):
        """Worktree creation with submodules should init the specified submodule."""
        info = git_repo_with_submodule
        repo, sub_path = info["repo"], info["sub_path"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)
        mgr = WorktreeManager(repo, wt_dir)

        wt_path = mgr.create("task-sub1", "cf/task-sub1", submodules=[sub_path])

        sub_in_wt = wt_path / sub_path
        assert sub_in_wt.exists()
        assert (sub_in_wt / "lib.py").exists()

    def test_create_without_submodule_leaves_empty(self, git_repo_with_submodule):
        """Worktree creation without submodules should not init submodules."""
        info = git_repo_with_submodule
        repo, sub_path = info["repo"], info["sub_path"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)
        mgr = WorktreeManager(repo, wt_dir)

        wt_path = mgr.create("task-nosub", "cf/task-nosub")
        assert not (wt_path / sub_path / "lib.py").exists()

    def test_create_with_invalid_submodule_raises(self, git_repo_with_submodule):
        """Worktree creation with invalid submodule path should raise."""
        info = git_repo_with_submodule
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)
        mgr = WorktreeManager(repo, wt_dir)

        with pytest.raises(subprocess.CalledProcessError):
            mgr.create("task-bad", "cf/task-bad", submodules=["nonexistent/path"])

    def test_create_non_git_ignores_submodules(self, non_git_dir):
        """Non-git mode should ignore submodules param."""
        wt_dir = non_git_dir / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(non_git_dir, wt_dir, is_git=False)
        result = mgr.create("task-ng", "cf/task-ng", submodules=["libs/core"])
        assert result == non_git_dir


from claude_flow.worker import Worker
from claude_flow.config import Config


class TestWorkerSubmoduleCommit:
    def _setup_worker(self, repo: Path):
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        (cf_dir / "logs").mkdir(exist_ok=True)
        cfg = Config()
        tm = TaskManager(repo)
        wt_dir = cf_dir / "worktrees"
        wt_dir.mkdir(exist_ok=True)
        wt = WorktreeManager(repo, wt_dir)
        worker = Worker(worker_id=0, project_root=repo,
                        task_manager=tm, worktree_manager=wt, config=cfg)
        return tm, wt, worker

    def test_auto_commit_submodule_then_main(self, git_repo_with_submodule):
        """Two-step commit: submodule first, then main project."""
        info = git_repo_with_submodule
        repo, sub_path = info["repo"], info["sub_path"]
        tm, wt, worker = self._setup_worker(repo)

        task = tm.add("Test sub commit", "modify submodule", submodules=[sub_path])
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        wt_path = wt.create(claimed.id, claimed.branch, submodules=claimed.submodules)

        # Simulate changes in submodule
        sub_in_wt = wt_path / sub_path
        (sub_in_wt / "lib.py").write_text("# modified\ndef hello():\n    return 'world'\n")

        result = worker._auto_commit(claimed, wt_path)
        assert result is True

        # Verify: submodule should have its own commit
        sub_log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(sub_in_wt), capture_output=True, text=True,
        )
        assert claimed.id in sub_log.stdout

        # Verify: main project should have a commit with updated pointer
        main_log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        assert claimed.id in main_log.stdout

    def test_auto_commit_no_submodule_changes(self, git_repo_with_submodule):
        """If submodule has no changes, skip submodule commit; main commit still works."""
        info = git_repo_with_submodule
        repo, sub_path = info["repo"], info["sub_path"]
        tm, wt, worker = self._setup_worker(repo)

        task = tm.add("No sub change", "only main change", submodules=[sub_path])
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        wt_path = wt.create(claimed.id, claimed.branch, submodules=claimed.submodules)
        (wt_path / "main_change.txt").write_text("main only")

        result = worker._auto_commit(claimed, wt_path)
        assert result is True

    def test_auto_commit_empty_submodules_list(self, git_repo_with_submodule):
        """Task with empty submodules list should use original commit logic."""
        info = git_repo_with_submodule
        repo = info["repo"]
        tm, wt, worker = self._setup_worker(repo)

        task = tm.add("No submodules", "normal task")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        wt_path = wt.create(claimed.id, claimed.branch)
        (wt_path / "file.txt").write_text("content")

        result = worker._auto_commit(claimed, wt_path)
        assert result is True


from unittest.mock import patch
from click.testing import CliRunner
from claude_flow.cli import main
from claude_flow.config import Config


class TestCliSubmodule:
    def test_task_add_with_submodule(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "add", "Test Task", "-p", "do something", "-s", "libs/mylib",
            ])
            assert result.exit_code == 0
            assert "Added" in result.output

        tm = TaskManager(repo)
        tasks = tm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].submodules == ["libs/mylib"]

    def test_task_add_multiple_submodules(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "add", "Multi Sub", "-p", "prompt",
                "-s", "libs/a", "-s", "libs/b",
            ])
            assert result.exit_code == 0

        tm = TaskManager(repo)
        tasks = tm.list_tasks()
        assert set(tasks[0].submodules) == {"libs/a", "libs/b"}

    def test_task_add_without_submodule(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "add", "Normal Task", "-p", "prompt",
            ])
            assert result.exit_code == 0

        tm = TaskManager(repo)
        tasks = tm.list_tasks()
        assert tasks[0].submodules == []

    def test_task_mini_with_submodule(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "mini", "fix bug", "-s", "libs/mylib",
            ])
            assert result.exit_code == 0

        tm = TaskManager(repo)
        tasks = tm.list_tasks()
        assert tasks[0].submodules == ["libs/mylib"]

    def test_task_show_displays_submodules(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        tm = TaskManager(repo)
        task = tm.add("Test", "prompt", submodules=["libs/mylib"])

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, ["task", "show", task.id])
            assert result.exit_code == 0
            assert "libs/mylib" in result.output


import json


class TestWebApiSubmodule:
    def _create_app(self, repo):
        from flask import Flask
        from claude_flow.web.api import api_bp
        from claude_flow.chat import ChatManager

        cfg = Config()
        cfg.save(repo)
        tm = TaskManager(repo)
        app = Flask(__name__)
        app.register_blueprint(api_bp)
        app.config["TASK_MANAGER"] = tm
        app.config["CF_CONFIG"] = cfg
        app.config["PROJECT_ROOT"] = repo
        app.config["IS_GIT"] = True
        app.config["CHAT_MANAGER"] = ChatManager(repo, cfg)
        return app, tm

    def test_create_task_with_submodules(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        (repo / ".claude-flow").mkdir(exist_ok=True)
        app, tm = self._create_app(repo)

        with app.test_client() as client:
            resp = client.post("/api/tasks", json={
                "title": "Test", "prompt": "do it",
                "submodules": ["libs/mylib"],
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["ok"] is True
            assert data["data"]["submodules"] == ["libs/mylib"]

    def test_create_task_without_submodules(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        (repo / ".claude-flow").mkdir(exist_ok=True)
        app, tm = self._create_app(repo)

        with app.test_client() as client:
            resp = client.post("/api/tasks", json={
                "title": "Test", "prompt": "do it",
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["data"]["submodules"] == []

    def test_create_mini_task_with_submodules(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        (repo / ".claude-flow").mkdir(exist_ok=True)
        app, tm = self._create_app(repo)

        with app.test_client() as client:
            resp = client.post("/api/mini-tasks", json={
                "title": "Mini Test", "prompt": "fix it",
                "submodules": ["libs/mylib"],
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["data"]["submodules"] == ["libs/mylib"]

    def test_get_submodules_list(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        (repo / ".claude-flow").mkdir(exist_ok=True)
        app, tm = self._create_app(repo)

        with app.test_client() as client:
            resp = client.get("/api/submodules")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "libs/mylib" in data["data"]

    def test_get_submodules_non_git(self, non_git_dir):
        app, tm = self._create_app(non_git_dir)
        app.config["IS_GIT"] = False

        with app.test_client() as client:
            resp = client.get("/api/submodules")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["data"] == []


class TestSubmoduleEdgeCases:
    def test_non_git_task_add_with_submodule_ignored(self, non_git_dir):
        """In non-git mode, submodules on task should be stored but not acted upon."""
        tm = TaskManager(non_git_dir)
        task = tm.add("Test", "prompt", submodules=["libs/core"])
        assert task.submodules == ["libs/core"]

        # Worker in non-git mode should not crash
        cfg = Config()
        wt = WorktreeManager(non_git_dir, non_git_dir / ".claude-flow" / "worktrees",
                             is_git=False)
        worker = Worker(0, non_git_dir, tm, wt, cfg, is_git=False)
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        # auto_commit in non-git mode: submodule dir won't exist, should skip gracefully
        result = worker._auto_commit(claimed, non_git_dir)
        # No changes made, should return False
        assert result is False

    def test_auto_commit_submodule_dir_missing(self, git_repo_with_submodule):
        """If submodule dir doesn't exist in worktree, skip gracefully."""
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        (cf_dir / "logs").mkdir(exist_ok=True)
        cfg = Config()
        tm = TaskManager(repo)
        wt_dir = cf_dir / "worktrees"
        wt_dir.mkdir(exist_ok=True)
        wt = WorktreeManager(repo, wt_dir)
        worker = Worker(0, repo, tm, wt, cfg)

        # Create task referencing a submodule that won't be initialized
        task = tm.add("Missing sub", "prompt", submodules=["nonexistent/sub"])
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        # Create worktree WITHOUT initializing submodule
        wt_path = wt.create(claimed.id, claimed.branch)
        (wt_path / "file.txt").write_text("content")

        # Should not crash, should still commit main project changes
        result = worker._auto_commit(claimed, wt_path)
        assert result is True

    def test_backward_compat_old_tasks_json(self, tmp_path):
        """tasks.json without submodules field should load without error."""
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tasks_file = cf_dir / "tasks.json"
        # Old format: no submodules key
        old_task = {
            "id": "task-old01",
            "title": "Old Task",
            "prompt": "old prompt",
            "status": "pending",
            "task_type": "normal",
            "branch": None,
            "plan_file": None,
            "worker_id": None,
            "created_at": "2026-01-01T00:00:00",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "priority": 0,
            "progress": None,
            "retry_count": 0,
            "plan_mode": None,
        }
        tasks_file.write_text(json.dumps([old_task]))

        tm = TaskManager(tmp_path)
        tasks = tm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].submodules == []
        assert tasks[0].title == "Old Task"
