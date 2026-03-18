"""Integration tests for multi-repo workspace full lifecycle.

These tests verify the complete task lifecycle in multi-repo mode:
  create composite -> modify files -> commit per-repo -> merge per-repo -> cleanup

Uses real git operations (in tmp_path) for full fidelity.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from claude_flow.config import Config
from claude_flow.models import ManagedRepo, ProjectMode, TaskStatus
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import MultiRepoWorktreeManager


# ===================================================================
# Helpers
# ===================================================================

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t.com",
    }
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(repo), check=check, capture_output=True, text=True, env=env,
    )


def _current_branch(repo: Path) -> str:
    result = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    return result.stdout.strip()


def _branch_exists(repo: Path, branch: str) -> bool:
    result = _git(repo, "branch", "--list", branch, check=False)
    return branch in result.stdout


def _log_subjects(repo: Path, n: int = 5) -> list[str]:
    """Return the last N commit subjects."""
    result = _git(repo, "log", f"-{n}", "--format=%s", check=False)
    return [s.strip() for s in result.stdout.strip().splitlines() if s.strip()]


def _make_manager(workspace: Path, repo_names: list[str]) -> MultiRepoWorktreeManager:
    managed = [ManagedRepo(path=name, main_branch="main") for name in repo_names]
    composite_dir = workspace / ".claude-flow" / "worktrees"
    composite_dir.mkdir(parents=True, exist_ok=True)
    return MultiRepoWorktreeManager(workspace, composite_dir, managed)


# ===================================================================
# Full lifecycle: create -> modify -> commit -> merge -> cleanup
# ===================================================================

class TestMultiRepoFullLifecycle:
    def test_full_lifecycle_two_repos(self, multi_repo_workspace):
        """Complete lifecycle: composite -> modify -> commit -> merge -> verify."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ["project-a", "project-b"])

        task_id = "task-lc01"

        # 1. Create composite
        composite = mgr.create_composite(task_id, {
            "project-a": "main",
            "project-b": "main",
        })
        assert composite.exists()

        # 2. Modify files in both repos
        (composite / "project-a" / "feature.py").write_text(
            "def feature():\n    return 'hello'\n"
        )
        (composite / "project-b" / "utils.py").write_text(
            "def helper():\n    return True\n"
        )

        # 3. Commit per-repo
        commit_results = mgr.commit_repos(task_id, composite,
                                          ["project-a", "project-b"])
        assert commit_results["project-a"] is True
        assert commit_results["project-b"] is True

        # 4. Merge per-repo
        merge_results = mgr.merge_repos(task_id, {
            "project-a": "main",
            "project-b": "main",
        })
        assert merge_results["project-a"] is True
        assert merge_results["project-b"] is True

        # 5. Verify new files are on main
        repo_a = ws["repos"]["project-a"]
        repo_b = ws["repos"]["project-b"]

        _git(repo_a, "checkout", "main")
        assert (repo_a / "feature.py").exists()
        assert (repo_a / "feature.py").read_text().strip() == "def feature():\n    return 'hello'"

        _git(repo_b, "checkout", "main")
        assert (repo_b / "utils.py").exists()

        # 6. Cleanup
        mgr.remove_composite(task_id, ["project-a", "project-b"])
        assert not composite.exists()
        assert not _branch_exists(repo_a, f"cf/{task_id}")
        assert not _branch_exists(repo_b, f"cf/{task_id}")

    def test_partial_repo_changes(self, multi_repo_workspace):
        """Only repos with actual changes should be committed and merged."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ["project-a", "project-b"])

        task_id = "task-partial"

        composite = mgr.create_composite(task_id, {
            "project-a": "main",
            "project-b": "main",
        })

        # Only modify project-a
        (composite / "project-a" / "only_a.txt").write_text("only in A")

        commit_results = mgr.commit_repos(task_id, composite,
                                          ["project-a", "project-b"])
        assert commit_results["project-a"] is True
        assert commit_results["project-b"] is False

        # Only merge project-a (skip project-b since no changes)
        changed_repos = {rp: "main" for rp, ok in commit_results.items() if ok}
        merge_results = mgr.merge_repos(task_id, changed_repos)
        assert merge_results.get("project-a") is True
        assert "project-b" not in merge_results

        # Verify
        _git(ws["repos"]["project-a"], "checkout", "main")
        assert (ws["repos"]["project-a"] / "only_a.txt").exists()

        # Cleanup
        mgr.remove_composite(task_id, ["project-a", "project-b"])

    def test_merge_to_feature_branch(self, multi_repo_workspace):
        """Merge to a non-main branch (feature-x)."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ["project-a"])

        task_id = "task-feat-target"

        # Create composite based on feature-x
        composite = mgr.create_composite(task_id, {
            "project-a": "feature-x",
        })

        (composite / "project-a" / "feat_impl.py").write_text("# feat impl\n")

        mgr.commit_repos(task_id, composite, ["project-a"])
        merge_results = mgr.merge_repos(task_id, {"project-a": "feature-x"})
        assert merge_results["project-a"] is True

        # Verify on feature-x
        repo_a = ws["repos"]["project-a"]
        _git(repo_a, "checkout", "feature-x")
        assert (repo_a / "feat_impl.py").exists()

        # Main should NOT have this file
        _git(repo_a, "checkout", "main")
        assert not (repo_a / "feat_impl.py").exists()

        mgr.remove_composite(task_id, ["project-a"])

    def test_nested_repo_lifecycle(self, multi_repo_workspace):
        """Lifecycle with a nested repo path (libs/core)."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ["libs/core"])

        task_id = "task-nested-lc"

        composite = mgr.create_composite(task_id, {
            "libs/core": "main",
        })

        (composite / "libs" / "core" / "new_module.py").write_text("# new\n")

        commit_results = mgr.commit_repos(task_id, composite, ["libs/core"])
        assert commit_results["libs/core"] is True

        merge_results = mgr.merge_repos(task_id, {"libs/core": "main"})
        assert merge_results["libs/core"] is True

        # Verify
        core_repo = ws["repos"]["libs/core"]
        _git(core_repo, "checkout", "main")
        assert (core_repo / "new_module.py").exists()

        mgr.remove_composite(task_id, ["libs/core"])


# ===================================================================
# Sequential tasks on same repos
# ===================================================================

class TestSequentialTasks:
    def test_two_tasks_same_repo(self, multi_repo_workspace):
        """Two sequential tasks on the same repo should both merge cleanly."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ["project-a"])

        # Task 1
        c1 = mgr.create_composite("task-seq1", {"project-a": "main"})
        (c1 / "project-a" / "task1.txt").write_text("task 1")
        mgr.commit_repos("task-seq1", c1, ["project-a"])
        r1 = mgr.merge_repos("task-seq1", {"project-a": "main"})
        assert r1["project-a"] is True
        mgr.remove_composite("task-seq1", ["project-a"])

        # Task 2 (after task 1 is merged)
        c2 = mgr.create_composite("task-seq2", {"project-a": "main"})
        (c2 / "project-a" / "task2.txt").write_text("task 2")
        mgr.commit_repos("task-seq2", c2, ["project-a"])
        r2 = mgr.merge_repos("task-seq2", {"project-a": "main"})
        assert r2["project-a"] is True
        mgr.remove_composite("task-seq2", ["project-a"])

        # Both files should exist
        repo_a = ws["repos"]["project-a"]
        _git(repo_a, "checkout", "main")
        assert (repo_a / "task1.txt").exists()
        assert (repo_a / "task2.txt").exists()


# ===================================================================
# Edge cases
# ===================================================================

class TestMultiRepoEdgeCases:
    def test_commit_empty_repos_list(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ["project-a"])

        composite = mgr.create_composite("task-empty", {"project-a": "main"})
        results = mgr.commit_repos("task-empty", composite, [])
        assert results == {}

        mgr.remove_composite("task-empty", ["project-a"])

    def test_list_active_reflects_state(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ["project-a", "project-b"])

        assert mgr.list_active() == []

        mgr.create_composite("task-x1", {"project-a": "main"})
        assert "task-x1" in mgr.list_active()

        mgr.create_composite("task-x2", {"project-b": "main"})
        assert sorted(mgr.list_active()) == ["task-x1", "task-x2"]

        mgr.remove_composite("task-x1", ["project-a"])
        assert mgr.list_active() == ["task-x2"]

        mgr.remove_composite("task-x2", ["project-b"])
        assert mgr.list_active() == []

    def test_all_three_repos(self, multi_repo_workspace):
        """Use all 3 repos including nested libs/core."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"],
                            ["project-a", "project-b", "libs/core"])

        task_id = "task-all3"
        composite = mgr.create_composite(task_id, {
            "project-a": "main",
            "project-b": "main",
            "libs/core": "main",
        })

        (composite / "project-a" / "a.txt").write_text("a")
        (composite / "project-b" / "b.txt").write_text("b")
        (composite / "libs" / "core" / "c.txt").write_text("c")

        commit_results = mgr.commit_repos(task_id, composite,
                                          ["project-a", "project-b", "libs/core"])
        assert all(commit_results.values())

        merge_results = mgr.merge_repos(task_id, {
            "project-a": "main",
            "project-b": "main",
            "libs/core": "main",
        })
        assert all(merge_results.values())

        # Verify all changes on main
        for name, repo_dir in ws["repos"].items():
            _git(repo_dir, "checkout", "main")

        assert (ws["repos"]["project-a"] / "a.txt").exists()
        assert (ws["repos"]["project-b"] / "b.txt").exists()
        assert (ws["repos"]["libs/core"] / "c.txt").exists()

        mgr.remove_composite(task_id, ["project-a", "project-b", "libs/core"])
