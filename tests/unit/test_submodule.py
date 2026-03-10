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
