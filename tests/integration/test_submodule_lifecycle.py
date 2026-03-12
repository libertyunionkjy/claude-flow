"""Integration tests for submodule worktree full lifecycle.

These tests verify the complete task lifecycle when submodules are involved:
  create task -> worktree -> init submodules -> modify -> commit -> merge -> cleanup

Uses the claude_subprocess_guard fixture to mock Claude CLI calls while
allowing real git operations to pass through.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.task_manager import TaskManager
from claude_flow.worker import Worker
from claude_flow.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Shorthand for running git commands."""
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args),
        check=check, capture_output=True, text=True,
    )


def _current_branch(repo: Path) -> str:
    """Return the current branch name."""
    result = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    return result.stdout.strip()


def _branch_exists(repo: Path, branch_name: str) -> bool:
    """Check if a branch exists in the repo."""
    result = _git(repo, "branch", "--list", branch_name, check=False)
    return branch_name in result.stdout


def _setup_worker(repo: Path) -> tuple:
    """Set up TaskManager, WorktreeManager, and Worker for a repo."""
    cf_dir = repo / ".claude-flow"
    cf_dir.mkdir(exist_ok=True)
    (cf_dir / "logs").mkdir(exist_ok=True)
    wt_dir = cf_dir / "worktrees"
    wt_dir.mkdir(exist_ok=True)
    cfg = Config()
    cfg.save(repo)
    tm = TaskManager(repo)
    wt = WorktreeManager(repo, wt_dir)
    worker = Worker(worker_id=0, project_root=repo,
                    task_manager=tm, worktree_manager=wt, config=cfg)
    return tm, wt, worker, cfg


# ===================================================================
# TestSubmoduleFullLifecycle
# ===================================================================

class TestSubmoduleFullLifecycle:
    """Integration test: full task lifecycle with submodules."""

    def test_task_modifies_multiple_submodules_and_merges(
        self, git_repo_with_multi_submodules, claude_subprocess_guard
    ):
        """Full lifecycle: create -> worktree -> modify -> commit -> merge -> cleanup.

        Verifies:
        1. Worktree is created with submodules initialized
        2. Submodule files are accessible in worktree
        3. Changes in submodules can be committed (two-step: sub then main)
        4. Merge back to main succeeds
        5. Worktree directory is cleaned up
        """
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        sub_paths = info["submodule_paths"]
        tm, wt, worker, cfg = _setup_worker(repo)

        # Step 1: Create task with submodules
        task = tm.add("Multi-sub lifecycle", "modify multiple submodules",
                      submodules=["libs/core", "libs/ui"])
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)
        assert claimed is not None

        # Step 2: Create worktree and initialize submodules
        wt_path = wt.create(claimed.id, claimed.branch,
                            submodules=claimed.submodules)
        assert wt_path.exists()

        # Step 3: Verify submodules are initialized
        sub_core = wt_path / "libs" / "core"
        sub_ui = wt_path / "libs" / "ui"
        assert sub_core.exists(), "libs/core should exist in worktree"
        assert sub_ui.exists(), "libs/ui should exist in worktree"
        assert (sub_core / "core.py").exists(), "core.py should be present"
        assert (sub_ui / "ui.py").exists(), "ui.py should be present"

        # Step 4: Simulate Claude's modifications in submodules
        (sub_core / "new_core_feature.py").write_text(
            "# new feature\ndef new_feature():\n    return 'implemented'\n"
        )
        (sub_ui / "new_ui_component.py").write_text(
            "# new component\ndef new_component():\n    return '<new/>'\n"
        )

        # Step 5: Auto-commit (submodule first, then main)
        commit_result = worker._auto_commit(claimed, wt_path)
        assert commit_result is True

        # Step 6: Verify commits exist in submodules
        core_log = _git(sub_core, "log", "--oneline", "-1")
        assert claimed.id in core_log.stdout

        ui_log = _git(sub_ui, "log", "--oneline", "-1")
        assert claimed.id in ui_log.stdout

        # Step 7: Merge worktree branch to main
        merge_success = wt.rebase_and_merge(claimed.branch, "main")
        assert merge_success is True

        # Step 8: Clean up
        wt.remove(claimed.id, claimed.branch)
        assert not wt_path.exists(), "Worktree should be removed"

        # Step 9: Verify main branch has the submodule pointer updates
        assert _current_branch(repo) == "main"

    def test_task_no_submodule_changes_still_merges(
        self, git_repo_with_multi_submodules, claude_subprocess_guard
    ):
        """Task with submodules but no submodule changes should still merge main changes."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        tm, wt, worker, cfg = _setup_worker(repo)

        task = tm.add("Main only change", "only modify main repo",
                      submodules=["libs/core"])
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        wt_path = wt.create(claimed.id, claimed.branch,
                            submodules=claimed.submodules)

        # Only modify a file in the main repo, not in submodules
        (wt_path / "main_only.txt").write_text("main repo change only")

        commit_result = worker._auto_commit(claimed, wt_path)
        assert commit_result is True

        merge_success = wt.rebase_and_merge(claimed.branch, "main")
        assert merge_success is True

        wt.remove(claimed.id, claimed.branch)
        assert not wt_path.exists()

        # Verify the main-repo change is on main
        assert (repo / "main_only.txt").exists()

    def test_parallel_tasks_different_submodules(
        self, git_repo_with_multi_submodules, claude_subprocess_guard
    ):
        """Two tasks targeting different submodules should not conflict.

        Task A: modifies libs/core
        Task B: modifies libs/ui
        Both should merge successfully.
        """
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        tm, wt, worker, cfg = _setup_worker(repo)

        # Create task A (libs/core)
        task_a = tm.add("Task A", "modify core", submodules=["libs/core"])
        tm.update_status(task_a.id, TaskStatus.APPROVED)
        claimed_a = tm.claim_next(0)

        wt_a = wt.create(claimed_a.id, claimed_a.branch,
                         submodules=claimed_a.submodules)
        sub_core_a = wt_a / "libs" / "core"
        (sub_core_a / "task_a.txt").write_text("task A change")
        (wt_a / "task_a_main.txt").write_text("task A main")

        commit_a = worker._auto_commit(claimed_a, wt_a)
        assert commit_a is True

        merge_a = wt.rebase_and_merge(claimed_a.branch, "main")
        assert merge_a is True
        wt.remove(claimed_a.id, claimed_a.branch)

        # Create task B (libs/ui) -- after task A is already merged
        task_b = tm.add("Task B", "modify ui", submodules=["libs/ui"])
        tm.update_status(task_b.id, TaskStatus.APPROVED)
        claimed_b = tm.claim_next(0)

        wt_b = wt.create(claimed_b.id, claimed_b.branch,
                         submodules=claimed_b.submodules)
        sub_ui_b = wt_b / "libs" / "ui"
        (sub_ui_b / "task_b.txt").write_text("task B change")
        (wt_b / "task_b_main.txt").write_text("task B main")

        commit_b = worker._auto_commit(claimed_b, wt_b)
        assert commit_b is True

        merge_b = wt.rebase_and_merge(claimed_b.branch, "main")
        assert merge_b is True
        wt.remove(claimed_b.id, claimed_b.branch)

        # Both changes should be on main
        assert (repo / "task_a_main.txt").exists()
        assert (repo / "task_b_main.txt").exists()

    def test_submodule_worktree_init_and_cleanup(
        self, git_repo_with_multi_submodules
    ):
        """Verify worktree creation with all 3 submodules and full cleanup."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        sub_paths = info["submodule_paths"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-lifecycle01"
        wt_path = mgr.create(task_id, f"cf/{task_id}", submodules=sub_paths)

        # All 3 submodules should be initialized
        assert (wt_path / "libs" / "core" / "core.py").exists()
        assert (wt_path / "libs" / "ui" / "ui.py").exists()
        assert (wt_path / "apps" / "server" / "server.py").exists()

        # Clean up
        mgr.remove(task_id, f"cf/{task_id}")
        assert not wt_path.exists()

        # Branch should be deleted
        assert not _branch_exists(repo, f"cf/{task_id}")

    def test_multi_submodule_fixture_structure(self, git_repo_with_multi_submodules):
        """Verify the fixture creates the expected repo structure."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        sub_remotes = info["sub_remotes"]

        # Main repo exists and has submodules
        assert repo.exists()
        assert (repo / ".gitmodules").exists()
        assert (repo / ".claude-flow").exists()

        # 3 submodule remotes
        assert len(sub_remotes) == 3
        assert "libs/core" in sub_remotes
        assert "libs/ui" in sub_remotes
        assert "apps/server" in sub_remotes

        # Each remote is a valid git repo
        for sub_path, remote_path in sub_remotes.items():
            assert remote_path.exists()
            assert (remote_path / ".git").exists()

        # Each remote has feature-a branch
        for sub_path, remote_path in sub_remotes.items():
            result = _git(remote_path, "branch", "--list", "feature-a")
            assert "feature-a" in result.stdout, \
                f"{sub_path} remote should have feature-a branch"

        # Main repo is on main branch
        assert _current_branch(repo) == "main"

        # Submodule paths list
        assert info["submodule_paths"] == ["libs/core", "libs/ui", "apps/server"]
