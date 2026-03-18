"""Tests for MultiRepoWorktreeManager."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from claude_flow.models import ManagedRepo
from claude_flow.worktree import MultiRepoWorktreeManager


# ===================================================================
# Helpers
# ===================================================================

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Shorthand for running git commands."""
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


def _log_oneline(repo: Path, n: int = 1) -> str:
    result = _git(repo, "log", f"--oneline", f"-{n}", check=False)
    return result.stdout.strip()


def _make_manager(workspace: Path, repos: dict[str, Path]) -> MultiRepoWorktreeManager:
    """Build a MultiRepoWorktreeManager from workspace fixture data."""
    managed = [
        ManagedRepo(path=name, main_branch="main")
        for name in repos
    ]
    composite_dir = workspace / ".claude-flow" / "worktrees"
    composite_dir.mkdir(parents=True, exist_ok=True)
    return MultiRepoWorktreeManager(workspace, composite_dir, managed)


# ===================================================================
# create_composite
# ===================================================================

class TestCreateComposite:
    def test_creates_composite_directory(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-001", {
            "project-a": "main",
            "project-b": "main",
        })

        assert composite.exists()
        assert (composite / "project-a").exists()
        assert (composite / "project-b").exists()
        # Each subdirectory should be a git worktree
        assert (composite / "project-a" / ".git").exists()
        assert (composite / "project-b" / ".git").exists()
        # README should be accessible
        assert (composite / "project-a" / "README.md").exists()

    def test_creates_branch_per_repo(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        mgr.create_composite("task-br1", {
            "project-a": "main",
        })

        # The task branch should exist in the original repo
        assert _branch_exists(ws["repos"]["project-a"], "cf/task-br1")

    def test_creates_worktree_based_on_specified_branch(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        # Create worktree based on feature-x branch
        composite = mgr.create_composite("task-feat", {
            "project-a": "feature-x",
        })

        # The worktree should be on cf/task-feat branch, based on feature-x
        wt_branch = _current_branch(composite / "project-a")
        assert wt_branch == "cf/task-feat"

    def test_creates_nested_repo_worktree(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-nested", {
            "libs/core": "main",
        })

        assert (composite / "libs" / "core").exists()
        assert (composite / "libs" / "core" / "README.md").exists()

    def test_subset_of_repos(self, multi_repo_workspace):
        """Only the specified repos should get worktrees."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-subset", {
            "project-a": "main",
        })

        assert (composite / "project-a").exists()
        assert not (composite / "project-b").exists()


# ===================================================================
# commit_repos
# ===================================================================

class TestCommitRepos:
    def test_no_changes_returns_false(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-nc", {
            "project-a": "main",
        })

        results = mgr.commit_repos("task-nc", composite, ["project-a"])
        assert results["project-a"] is False

    def test_with_changes_commits(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-chg", {
            "project-a": "main",
        })

        # Write a new file in the worktree
        (composite / "project-a" / "new_file.py").write_text("# new\n")

        results = mgr.commit_repos("task-chg", composite, ["project-a"])
        assert results["project-a"] is True

        # Verify commit message
        log = _log_oneline(composite / "project-a")
        assert "cf/task-chg" in log

    def test_partial_changes(self, multi_repo_workspace):
        """Only repos with changes should have commits."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-partial", {
            "project-a": "main",
            "project-b": "main",
        })

        # Only modify project-a
        (composite / "project-a" / "change.txt").write_text("modified")

        results = mgr.commit_repos("task-partial", composite,
                                   ["project-a", "project-b"])
        assert results["project-a"] is True
        assert results["project-b"] is False

    def test_missing_repo_dir(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = ws["workspace"] / ".claude-flow" / "worktrees" / "task-miss"
        composite.mkdir(parents=True)

        results = mgr.commit_repos("task-miss", composite, ["nonexistent-repo"])
        assert results["nonexistent-repo"] is False


# ===================================================================
# merge_repos
# ===================================================================

class TestMergeRepos:
    def test_merge_to_target_branch(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-mrg1", {
            "project-a": "main",
        })

        # Make a change and commit
        (composite / "project-a" / "merged_file.txt").write_text("to be merged")
        mgr.commit_repos("task-mrg1", composite, ["project-a"])

        # Merge back to main
        results = mgr.merge_repos("task-mrg1", {"project-a": "main"})
        assert results["project-a"] is True

        # Verify the file is on main
        assert (ws["repos"]["project-a"] / "merged_file.txt").exists()

        # Full cleanup: remove_composite deletes worktree and branch
        mgr.remove_composite("task-mrg1", ["project-a"])
        assert not _branch_exists(ws["repos"]["project-a"], "cf/task-mrg1")

    def test_merge_conflict_returns_false(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        # Create conflicting change on main
        repo_a = ws["repos"]["project-a"]
        (repo_a / "conflict.txt").write_text("main version")
        _git(repo_a, "add", ".")
        _git(repo_a, "commit", "-m", "main side conflict")

        # Create worktree and make conflicting change
        composite = mgr.create_composite("task-conflict", {
            "project-a": "main~1",  # base on parent of HEAD
        })
        (composite / "project-a" / "conflict.txt").write_text("task version")
        mgr.commit_repos("task-conflict", composite, ["project-a"])

        # Try to merge (should conflict)
        results = mgr.merge_repos("task-conflict", {"project-a": "main"})
        assert results["project-a"] is False

    def test_merge_multiple_repos(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-multi", {
            "project-a": "main",
            "project-b": "main",
        })

        (composite / "project-a" / "a_change.txt").write_text("a")
        (composite / "project-b" / "b_change.txt").write_text("b")
        mgr.commit_repos("task-multi", composite,
                         ["project-a", "project-b"])

        results = mgr.merge_repos("task-multi", {
            "project-a": "main",
            "project-b": "main",
        })
        assert results["project-a"] is True
        assert results["project-b"] is True

        assert (ws["repos"]["project-a"] / "a_change.txt").exists()
        assert (ws["repos"]["project-b"] / "b_change.txt").exists()

    def test_merge_to_feature_branch(self, multi_repo_workspace):
        """Merge to a non-main branch (feature-x)."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-feat-merge", {
            "project-a": "feature-x",
        })

        (composite / "project-a" / "feat_change.txt").write_text("feat")
        mgr.commit_repos("task-feat-merge", composite, ["project-a"])

        results = mgr.merge_repos("task-feat-merge", {"project-a": "feature-x"})
        assert results["project-a"] is True

        # Verify on feature-x branch
        _git(ws["repos"]["project-a"], "checkout", "feature-x")
        assert (ws["repos"]["project-a"] / "feat_change.txt").exists()


# ===================================================================
# remove_composite
# ===================================================================

class TestRemoveComposite:
    def test_removes_worktree_and_branch(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        composite = mgr.create_composite("task-rm", {
            "project-a": "main",
            "project-b": "main",
        })
        assert composite.exists()

        mgr.remove_composite("task-rm", ["project-a", "project-b"])

        assert not composite.exists()
        assert not _branch_exists(ws["repos"]["project-a"], "cf/task-rm")
        assert not _branch_exists(ws["repos"]["project-b"], "cf/task-rm")

    def test_remove_nonexistent_is_safe(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        # Should not raise
        mgr.remove_composite("task-noexist", ["project-a"])


# ===================================================================
# list_active
# ===================================================================

class TestListActive:
    def test_empty(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        assert mgr.list_active() == []

    def test_with_composites(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        mgr.create_composite("task-a1", {"project-a": "main"})
        mgr.create_composite("task-b2", {"project-b": "main"})

        active = mgr.list_active()
        assert sorted(active) == ["task-a1", "task-b2"]

    def test_after_removal(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        mgr.create_composite("task-x", {"project-a": "main"})
        mgr.remove_composite("task-x", ["project-a"])

        assert mgr.list_active() == []


# ===================================================================
# get_repo_branches
# ===================================================================

class TestGetRepoBranches:
    def test_returns_branches(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        branches = mgr.get_repo_branches("project-a")
        assert "main" in branches
        assert "feature-x" in branches

    def test_nonexistent_repo_raises(self, multi_repo_workspace):
        """Querying branches for a repo path that doesn't exist on disk should raise."""
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        with pytest.raises(Exception):
            mgr.get_repo_branches("nonexistent")


# ===================================================================
# get_repo_status
# ===================================================================

class TestGetRepoStatus:
    def test_returns_status(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        status = mgr.get_repo_status("project-a")
        assert status["current_branch"] == "main"
        assert status["has_changes"] is False
        assert status["remote_url"] == ""  # local repo has no remote

    def test_detects_changes(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        # Make an uncommitted change
        (ws["repos"]["project-a"] / "dirty.txt").write_text("dirty")

        status = mgr.get_repo_status("project-a")
        assert status["has_changes"] is True


# ===================================================================
# get_repo_worktrees
# ===================================================================

class TestGetRepoWorktrees:
    def test_initial_worktree(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        worktrees = mgr.get_repo_worktrees("project-a")
        # Should have at least the main worktree
        assert len(worktrees) >= 1

    def test_with_composite_worktree(self, multi_repo_workspace):
        ws = multi_repo_workspace
        mgr = _make_manager(ws["workspace"], ws["repos"])

        mgr.create_composite("task-wt1", {"project-a": "main"})

        worktrees = mgr.get_repo_worktrees("project-a")
        # Main worktree + task worktree
        assert len(worktrees) >= 2
        # One of them should be on cf/task-wt1 branch
        branches = [wt.get("branch", "") for wt in worktrees]
        assert "cf/task-wt1" in branches


# ===================================================================
# push_repos (unit test with mock)
# ===================================================================

class TestPushRepos:
    def test_skips_repos_without_auto_push(self, multi_repo_workspace):
        ws = multi_repo_workspace
        # All repos have auto_push=False by default
        mgr = _make_manager(ws["workspace"], ws["repos"])

        results = mgr.push_repos("task-push", ["project-a"])
        # Should be empty dict since auto_push is False
        assert results == {}

    def test_push_no_remote(self, multi_repo_workspace):
        ws = multi_repo_workspace
        managed = [
            ManagedRepo(path="project-a", main_branch="main", auto_push=True),
        ]
        composite_dir = ws["workspace"] / ".claude-flow" / "worktrees"
        mgr = MultiRepoWorktreeManager(ws["workspace"], composite_dir, managed)

        results = mgr.push_repos("task-push2", ["project-a"])
        # No remote exists, should fail
        assert results["project-a"] is False
