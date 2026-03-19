"""Tests for conflict analysis, resolve, and worktree cleanup on reset/retry."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import WorktreeManager


# -- TaskManager.clear_branch ------------------------------------------------

class TestClearBranch:
    def _make_manager(self, tmp_path: Path) -> TaskManager:
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        return TaskManager(tmp_path)

    def test_clear_branch_sets_none(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add("Test", "prompt")
        # Simulate branch assignment
        mgr.update_status(task.id, TaskStatus.RUNNING)
        t = mgr.get(task.id)
        # claim_next sets branch, but let's set it manually via internal update
        tasks = mgr._load()
        for t in tasks:
            if t.id == task.id:
                t.branch = "cf/test-branch"
        mgr._save(tasks)

        updated = mgr.clear_branch(task.id)
        assert updated is not None
        assert updated.branch is None

        # Verify persistence
        reloaded = mgr.get(task.id)
        assert reloaded.branch is None

    def test_clear_branch_nonexistent_returns_none(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.clear_branch("nonexistent")
        assert result is None


# -- WorktreeManager.create defensive check ----------------------------------

class TestCreateDefensiveCheck:
    def test_create_cleans_stale_worktree(self, git_repo):
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)

        # First creation
        wt_path = mgr.create("task-def1", "cf/task-def1")
        assert wt_path.exists()

        # Remove worktree but leave branch (simulating partial cleanup)
        subprocess.run(
            ["git", "-C", str(git_repo), "worktree", "remove", str(wt_path), "--force"],
            check=True, capture_output=True,
        )
        assert not wt_path.exists()
        # Branch still exists
        result = subprocess.run(
            ["git", "-C", str(git_repo), "branch", "--list", "cf/task-def1"],
            capture_output=True, text=True,
        )
        assert "cf/task-def1" in result.stdout

        # Second creation should succeed (defensive check deletes stale branch)
        wt_path2 = mgr.create("task-def1", "cf/task-def1")
        assert wt_path2.exists()

    def test_create_cleans_stale_directory_and_branch(self, git_repo):
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)

        # First creation
        wt_path = mgr.create("task-def2", "cf/task-def2")
        assert wt_path.exists()

        # Remove and recreate (defensive check should handle existing worktree)
        mgr.remove("task-def2", "cf/task-def2")
        # Manually create a stale directory to simulate incomplete cleanup
        wt_path.mkdir(parents=True, exist_ok=True)

        # Should still succeed
        wt_path2 = mgr.create("task-def2", "cf/task-def2")
        assert wt_path2.exists()
        assert (wt_path2 / "README.md").exists()


# -- Web API reset/retry with worktree cleanup --------------------------------

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
    return web_app.test_client()


@pytest.fixture
def tm(web_app) -> TaskManager:
    return web_app.config["TASK_MANAGER"]


class TestResetWithCleanup:
    def test_reset_failed_cleans_worktree(self, client, tm, cf_project):
        """Reset a FAILED task with branch should clean up worktree."""
        # Create task and set up worktree
        task = tm.add("Test Reset", "prompt")
        cfg = Config.load(cf_project)
        wt_mgr = WorktreeManager(cf_project, cf_project / cfg.worktree_dir, is_git=True)
        wt_path = wt_mgr.create(task.id, f"cf/{task.id}")
        assert wt_path.exists()

        # Simulate failed state with branch
        tm.update_status(task.id, TaskStatus.FAILED, error="CONFLICT")
        tasks = tm._load()
        for t in tasks:
            if t.id == task.id:
                t.branch = f"cf/{task.id}"
        tm._save(tasks)

        # Reset via API
        resp = client.post(f"/api/tasks/{task.id}/reset")
        data = resp.get_json()
        assert data["ok"] is True

        # Worktree should be cleaned up
        assert not wt_path.exists()

        # Branch field should be cleared
        updated = tm.get(task.id)
        assert updated.branch is None
        assert updated.status == TaskStatus.PENDING

    def test_reset_failed_without_branch_skips_cleanup(self, client, tm):
        """Reset a FAILED task without branch should work without cleanup."""
        task = tm.add("No Branch", "prompt")
        tm.update_status(task.id, TaskStatus.FAILED, error="some error")

        resp = client.post(f"/api/tasks/{task.id}/reset")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "pending"


class TestRetryAllWithCleanup:
    def test_retry_all_cleans_worktrees(self, client, tm, cf_project):
        """Retry all should clean up worktrees for all failed tasks."""
        cfg = Config.load(cf_project)
        wt_mgr = WorktreeManager(cf_project, cf_project / cfg.worktree_dir, is_git=True)

        # Create two failed tasks with worktrees
        t1 = tm.add("Task 1", "prompt1")
        t2 = tm.add("Task 2", "prompt2")

        wt1 = wt_mgr.create(t1.id, f"cf/{t1.id}")
        wt2 = wt_mgr.create(t2.id, f"cf/{t2.id}")

        tm.update_status(t1.id, TaskStatus.FAILED, error="CONFLICT")
        tm.update_status(t2.id, TaskStatus.FAILED, error="CONFLICT")

        # Set branches
        tasks = tm._load()
        for t in tasks:
            if t.id == t1.id:
                t.branch = f"cf/{t1.id}"
            elif t.id == t2.id:
                t.branch = f"cf/{t2.id}"
        tm._save(tasks)

        # Retry all
        resp = client.post("/api/retry-all")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["retried"] == 2

        # Both worktrees cleaned
        assert not wt1.exists()
        assert not wt2.exists()

        # Both branches cleared and status reset
        for tid in [t1.id, t2.id]:
            updated = tm.get(tid)
            assert updated.branch is None
            assert updated.status == TaskStatus.APPROVED


# -- Conflict Analysis API ---------------------------------------------------

class TestConflictAnalysis:
    def test_analysis_wrong_status(self, client, tm):
        """Should reject analysis for non-CONFLICT tasks."""
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.FAILED, error="some other error")
        resp = client.get(f"/api/tasks/{task.id}/conflict-analysis")
        data = resp.get_json()
        assert data["ok"] is False

    def test_analysis_pending_task(self, client, tm):
        """Should reject analysis for pending tasks."""
        task = tm.add("Test", "prompt")
        resp = client.get(f"/api/tasks/{task.id}/conflict-analysis")
        data = resp.get_json()
        assert data["ok"] is False

    def test_analysis_no_branch(self, client, tm):
        """Should reject analysis when task has no branch."""
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.FAILED, error="CONFLICT")
        resp = client.get(f"/api/tasks/{task.id}/conflict-analysis")
        data = resp.get_json()
        assert data["ok"] is False

    def test_analysis_returns_data(self, client, tm, cf_project):
        """Should return analysis data for CONFLICT task with worktree."""
        cfg = Config.load(cf_project)
        wt_mgr = WorktreeManager(cf_project, cf_project / cfg.worktree_dir, is_git=True)

        task = tm.add("Conflict Task", "prompt")
        wt_path = wt_mgr.create(task.id, f"cf/{task.id}")

        # Make a change in worktree
        (wt_path / "conflict_file.txt").write_text("branch content")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "branch change"],
                       check=True, capture_output=True)

        # Set FAILED + CONFLICT with branch
        tm.update_status(task.id, TaskStatus.FAILED, error="CONFLICT")
        tasks = tm._load()
        for t in tasks:
            if t.id == task.id:
                t.branch = f"cf/{task.id}"
        tm._save(tasks)

        # Disable skip_permissions to avoid real claude AI call in analysis
        cfg.skip_permissions = False
        cfg.save(cf_project)
        # Reload config in app
        client.application.config["CF_CONFIG"] = Config.load(cf_project)

        resp = client.get(f"/api/tasks/{task.id}/conflict-analysis")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["task_id"] == task.id
        assert data["data"]["branch"] == f"cf/{task.id}"
        assert "diff_stat" in data["data"]
        assert "commits" in data["data"]
        # AI analysis should be None since skip_permissions is disabled
        assert data["data"]["ai_analysis"] is None


# -- Resolve Conflict API ----------------------------------------------------

class TestResolveConflict:
    def test_resolve_wrong_status(self, client, tm):
        """Should reject resolve for non-CONFLICT tasks."""
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.FAILED, error="some other error")
        resp = client.post(f"/api/tasks/{task.id}/resolve-conflict")
        data = resp.get_json()
        assert data["ok"] is False

    def test_resolve_no_branch(self, client, tm):
        """Should reject resolve when task has no branch."""
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.FAILED, error="CONFLICT")
        resp = client.post(f"/api/tasks/{task.id}/resolve-conflict")
        data = resp.get_json()
        assert data["ok"] is False

    def test_resolve_nonexistent_task(self, client):
        """Should return 404 for nonexistent task."""
        resp = client.post("/api/tasks/nonexistent/resolve-conflict")
        assert resp.status_code == 404

    def test_resolve_success_merges_and_cleans(self, client, tm, cf_project):
        """Successful resolve should merge, clean worktree, and set DONE."""
        cfg = Config.load(cf_project)
        wt_mgr = WorktreeManager(cf_project, cf_project / cfg.worktree_dir, is_git=True)

        task = tm.add("Resolvable Task", "prompt")
        wt_path = wt_mgr.create(task.id, f"cf/{task.id}")

        # Make a non-conflicting change in worktree
        (wt_path / "new_feature.txt").write_text("new feature")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "add feature"],
                       check=True, capture_output=True)

        # Set FAILED + CONFLICT with branch
        tm.update_status(task.id, TaskStatus.FAILED, error="CONFLICT")
        tasks = tm._load()
        for t in tasks:
            if t.id == task.id:
                t.branch = f"cf/{task.id}"
        tm._save(tasks)

        # Call resolve -- since there's no actual conflict, merge should succeed
        resp = client.post(f"/api/tasks/{task.id}/resolve-conflict")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["success"] is True

        # Verify cleanup
        assert not wt_path.exists()
        updated = tm.get(task.id)
        assert updated.status == TaskStatus.DONE
        assert updated.branch is None
