"""Tests for submodule worktree integration (branch management, merge, push).

These tests cover the enhanced submodule workflows:
  - Named branches (cf/{task_id}) inside submodules instead of detached HEAD
  - Internal merge of cf/{task_id} back to target branch within submodules
  - Push of submodule changes to remote
  - Task.sub_branches model field
  - CLI --sub-branch parameter parsing
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Shorthand for running git commands.

    Automatically sets user.email and user.name via -c flags so that
    commits work in submodules where these may not be configured.
    """
    return subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.email=test@test.com",
         "-c", "user.name=Test"] + list(args),
        check=check, capture_output=True, text=True,
    )


def _current_branch(repo: Path) -> str:
    """Return the current branch name (empty string if detached HEAD)."""
    result = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    return result.stdout.strip()


def _branch_exists(repo: Path, branch_name: str) -> bool:
    """Check if a branch exists in the repo."""
    result = _git(repo, "branch", "--list", branch_name, check=False)
    return branch_name in result.stdout


def _file_exists_on_branch(repo: Path, branch_name: str, file_path: str) -> bool:
    """Check if a file exists on a given branch."""
    result = _git(repo, "show", f"{branch_name}:{file_path}", check=False)
    return result.returncode == 0


def _commit_count(repo: Path, branch_name: str) -> int:
    """Count commits on a branch."""
    result = _git(repo, "rev-list", "--count", branch_name, check=False)
    if result.returncode != 0:
        return 0
    return int(result.stdout.strip())


def _make_task(task_id: str, submodules: list[str] | None = None,
               sub_branches: dict[str, str] | None = None,
               title: str = "test task") -> Task:
    """Create a minimal Task object for merge/push helper tests."""
    return Task(
        id=task_id,
        title=title,
        prompt="test",
        submodules=submodules or [],
        sub_branches=sub_branches or {},
    )


# ===================================================================
# TestSubmoduleBranchInit
# ===================================================================

class TestSubmoduleBranchInit:
    """Test that submodules get named branches instead of detached HEAD."""

    def test_init_creates_named_branch(self, git_repo_with_multi_submodules):
        """After init, submodule should be on cf/{task_id} branch, not detached HEAD."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        sub_paths = info["submodule_paths"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-branch01"
        wt_path = mgr.create(task_id, f"cf/{task_id}", submodules=sub_paths)

        # Each submodule should be on cf/{task_id} branch (not detached HEAD)
        for sub_path in sub_paths:
            sub_dir = wt_path / sub_path
            assert sub_dir.exists(), f"Submodule {sub_path} should exist in worktree"
            assert any(sub_dir.iterdir()), f"Submodule {sub_path} should have files"
            branch = _current_branch(sub_dir)
            assert branch == f"cf/{task_id}", \
                f"Submodule {sub_path} should be on cf/{task_id}, got {branch}"

        mgr.remove(task_id, f"cf/{task_id}")

    def test_init_with_base_branch(self, git_repo_with_multi_submodules):
        """With sub_branches specified, cf/{task_id} should be based on that branch."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-base01"
        # libs/core based on origin/feature-a; others on default HEAD
        wt_path = mgr.create(
            task_id, f"cf/{task_id}",
            submodules=["libs/core", "libs/ui", "apps/server"],
            sub_branches={"libs/core": "origin/feature-a"},
        )

        sub_core = wt_path / "libs" / "core"
        # Verify: cf/{task_id} branch exists and has feature-a files
        branch = _current_branch(sub_core)
        assert branch == f"cf/{task_id}"
        assert (sub_core / "feature_a.py").exists(), \
            "feature-a files should be present on cf/ branch based on feature-a"

        # libs/ui should also be on named branch but without feature-a files
        sub_ui = wt_path / "libs" / "ui"
        assert _current_branch(sub_ui) == f"cf/{task_id}"
        assert not (sub_ui / "feature_a_ui.py").exists()

        mgr.remove(task_id, f"cf/{task_id}")

    def test_init_mixed_branches(self, git_repo_with_multi_submodules):
        """Some submodules with base branch, some without.

        libs/core based on feature-a, libs/ui on default HEAD.
        """
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-mixed01"
        wt_path = mgr.create(
            task_id, f"cf/{task_id}",
            submodules=["libs/core", "libs/ui"],
            sub_branches={"libs/core": "origin/feature-a"},
        )

        sub_core = wt_path / "libs" / "core"
        sub_ui = wt_path / "libs" / "ui"

        # Verify libs/core has feature-a content
        assert (sub_core / "feature_a.py").exists()
        assert _current_branch(sub_core) == f"cf/{task_id}"

        # Verify libs/ui is on named branch but does NOT have feature-a content
        assert _current_branch(sub_ui) == f"cf/{task_id}"
        assert not (sub_ui / "feature_a_ui.py").exists()

        mgr.remove(task_id, f"cf/{task_id}")

    def test_init_nonexistent_branch_raises(self, git_repo_with_multi_submodules):
        """Specifying a non-existent branch should raise CalledProcessError."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-noexist01"
        with pytest.raises(subprocess.CalledProcessError):
            mgr.create(
                task_id, f"cf/{task_id}",
                submodules=["libs/core"],
                sub_branches={"libs/core": "nonexistent-branch"},
            )

        # Clean up partially created worktree
        mgr.remove(task_id, f"cf/{task_id}")


# ===================================================================
# TestSubmoduleMerge
# ===================================================================

class TestSubmoduleMerge:
    """Test submodule internal merge flow using merge_submodules()."""

    def test_merge_to_target_branch(self, git_repo_with_multi_submodules):
        """cf/{task_id} changes should merge back to target branch via merge_submodules()."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-merge01"
        # Create worktree; libs/core gets cf/{task_id} based on origin/feature-a
        wt_path = mgr.create(
            task_id, f"cf/{task_id}",
            submodules=["libs/core"],
            sub_branches={"libs/core": "origin/feature-a"},
        )

        sub_core = wt_path / "libs" / "core"
        assert _current_branch(sub_core) == f"cf/{task_id}"

        # Simulate Claude's modifications on cf/ branch
        new_file = sub_core / "claude_changes.py"
        new_file.write_text("# changes by claude\ndef new_func():\n    return 42\n")
        _git(sub_core, "add", ".")
        _git(sub_core, "commit", "-m", f"feat({task_id}): add new_func")

        # Create a local "feature-a" branch for merge target
        _git(sub_core, "branch", "feature-a", "origin/feature-a", check=False)

        task = _make_task(task_id, submodules=["libs/core"],
                          sub_branches={"libs/core": "feature-a"})
        result = mgr.merge_submodules(wt_path, task)
        assert result is True

        # Verify target branch contains the new file
        assert _current_branch(sub_core) == "feature-a"
        assert (sub_core / "claude_changes.py").exists()

        mgr.remove(task_id, f"cf/{task_id}")

    def test_merge_preserves_commits_reachable(self, git_repo_with_multi_submodules):
        """After merge, commits should be reachable from target branch."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-reach01"
        wt_path = mgr.create(
            task_id, f"cf/{task_id}",
            submodules=["libs/core"],
        )

        sub_core = wt_path / "libs" / "core"
        assert _current_branch(sub_core) == f"cf/{task_id}"

        # Make a commit
        (sub_core / "reachable.txt").write_text("reachable content")
        _git(sub_core, "add", ".")
        _git(sub_core, "commit", "-m", "reachable commit")

        # Get the commit hash
        commit_hash = _git(sub_core, "rev-parse", "HEAD").stdout.strip()

        # Merge to main using merge_submodules()
        task = _make_task(task_id, submodules=["libs/core"],
                          sub_branches={"libs/core": "main"})
        result = mgr.merge_submodules(wt_path, task)
        assert result is True

        # Verify commit is reachable from main
        log_result = _git(sub_core, "log", "--oneline", "main")
        assert commit_hash[:7] in log_result.stdout

        mgr.remove(task_id, f"cf/{task_id}")

    def test_merge_without_sub_branches_skips(self, git_repo_with_multi_submodules):
        """Submodules without sub_branches entry should not be merged internally."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-skip01"
        wt_path = mgr.create(
            task_id, f"cf/{task_id}",
            submodules=["libs/core", "libs/ui"],
        )

        sub_core = wt_path / "libs" / "core"
        sub_ui = wt_path / "libs" / "ui"

        # Both are on cf/{task_id} (auto-created by _init_submodules)
        assert _current_branch(sub_core) == f"cf/{task_id}"
        assert _current_branch(sub_ui) == f"cf/{task_id}"

        # Make changes in both
        (sub_core / "skip_test.txt").write_text("core change")
        _git(sub_core, "add", ".")
        _git(sub_core, "commit", "-m", "core change")

        (sub_ui / "skip_test.txt").write_text("ui change")
        _git(sub_ui, "add", ".")
        _git(sub_ui, "commit", "-m", "ui change")

        # Only libs/core has a target branch -- libs/ui should be skipped
        task = _make_task(task_id, submodules=["libs/core", "libs/ui"],
                          sub_branches={"libs/core": "main"})
        result = mgr.merge_submodules(wt_path, task)
        assert result is True

        # libs/core should now be on main (merged)
        assert _current_branch(sub_core) == "main"

        # libs/ui should still be on cf/{task_id} -- no merge happened
        assert _current_branch(sub_ui) == f"cf/{task_id}"

        mgr.remove(task_id, f"cf/{task_id}")

    def test_merge_conflict_returns_false(self, git_repo_with_multi_submodules):
        """Merge conflict in submodule should return False."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-conflict01"
        wt_path = mgr.create(
            task_id, f"cf/{task_id}",
            submodules=["libs/core"],
        )

        sub_core = wt_path / "libs" / "core"
        assert _current_branch(sub_core) == f"cf/{task_id}"

        # Modify core.py on cf/ branch
        (sub_core / "core.py").write_text("# modified by cf branch\ndef core_func():\n    return 'cf'\n")
        _git(sub_core, "add", ".")
        _git(sub_core, "commit", "-m", "cf change")

        # Also modify core.py on main branch (conflicting change)
        _git(sub_core, "checkout", "main")
        (sub_core / "core.py").write_text("# modified by main\ndef core_func():\n    return 'main'\n")
        _git(sub_core, "add", ".")
        _git(sub_core, "commit", "-m", "main change")

        # Go back to cf/ branch so merge_submodules can checkout main and merge
        _git(sub_core, "checkout", f"cf/{task_id}")

        # Attempt merge -- should conflict and return False
        task = _make_task(task_id, submodules=["libs/core"],
                          sub_branches={"libs/core": "main"})
        result = mgr.merge_submodules(wt_path, task)
        assert result is False

        mgr.remove(task_id, f"cf/{task_id}")

    def test_cleanup_removes_temp_branch(self, git_repo_with_multi_submodules):
        """After successful merge, cf/{task_id} branch should be deleted."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-cleanup01"
        wt_path = mgr.create(
            task_id, f"cf/{task_id}",
            submodules=["libs/core"],
        )

        sub_core = wt_path / "libs" / "core"
        assert _current_branch(sub_core) == f"cf/{task_id}"

        # Make a change and commit
        (sub_core / "cleanup_test.txt").write_text("test")
        _git(sub_core, "add", ".")
        _git(sub_core, "commit", "-m", "cleanup test commit")

        # Merge using merge_submodules() -- should clean up temp branch
        task = _make_task(task_id, submodules=["libs/core"],
                          sub_branches={"libs/core": "main"})
        result = mgr.merge_submodules(wt_path, task)
        assert result is True

        # Verify temp branch is gone
        assert not _branch_exists(sub_core, f"cf/{task_id}")

        mgr.remove(task_id, f"cf/{task_id}")


# ===================================================================
# TestSubmodulePush
# ===================================================================

class TestSubmodulePush:
    """Test submodule push functionality via push_submodules()."""

    def test_push_to_remote(self, git_repo_with_multi_submodules):
        """Submodules with remotes should have push attempted via push_submodules()."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-push01"
        wt_path = mgr.create(task_id, f"cf/{task_id}", submodules=["libs/core"])

        sub_core = wt_path / "libs" / "core"
        assert _current_branch(sub_core) == f"cf/{task_id}"

        # Make a change
        (sub_core / "pushed.txt").write_text("pushed content")
        _git(sub_core, "add", ".")
        _git(sub_core, "commit", "-m", "push test")

        # push_submodules should attempt to push (may fail for non-bare remote, which is fine)
        task = _make_task(task_id, submodules=["libs/core"])
        mgr.push_submodules(wt_path, task)  # should not raise

        mgr.remove(task_id, f"cf/{task_id}")

    def test_push_skips_no_remote(self, git_repo_with_multi_submodules):
        """Submodules without remotes should be skipped during push."""
        info = git_repo_with_multi_submodules
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)

        task_id = "task-nopush01"
        wt_path = mgr.create(task_id, f"cf/{task_id}", submodules=["libs/core"])

        sub_core = wt_path / "libs" / "core"

        # Remove remote to simulate no-remote scenario
        _git(sub_core, "remote", "remove", "origin", check=False)

        remote_result = _git(sub_core, "remote", check=False)
        assert "origin" not in remote_result.stdout, "Remote should be removed"

        # push_submodules should skip this submodule without error
        task = _make_task(task_id, submodules=["libs/core"])
        mgr.push_submodules(wt_path, task)  # should not raise

        mgr.remove(task_id, f"cf/{task_id}")


# ===================================================================
# TestSubBranchesModel
# ===================================================================

class TestSubBranchesModel:
    """Test Task model sub_branches field."""

    def test_default_empty_dict(self):
        """Task.sub_branches should default to empty dict."""
        task = Task(id="test", title="t", prompt="p")
        assert task.sub_branches == {}

    def test_serialization_roundtrip(self):
        """sub_branches should survive to_dict/from_dict roundtrip."""
        task = Task(id="test-rt", title="Roundtrip", prompt="p",
                    submodules=["libs/core", "libs/ui"],
                    sub_branches={"libs/core": "feature-a"})
        d = task.to_dict()
        restored = Task.from_dict(d)
        assert restored.submodules == ["libs/core", "libs/ui"]
        assert restored.sub_branches == {"libs/core": "feature-a"}

    def test_backward_compat(self):
        """Old tasks.json without sub_branches should load with default {}."""
        old_task = {
            "id": "task-old99",
            "title": "Old Task",
            "prompt": "old prompt",
            "status": "pending",
            "task_type": "normal",
            "branch": None,
            "plan_file": None,
            "worker_id": None,
            "created_at": datetime.now().isoformat(),
            "started_at": None,
            "completed_at": None,
            "error": None,
            "priority": 0,
            "progress": None,
            "retry_count": 0,
            "plan_mode": None,
        }
        task = Task.from_dict(old_task)
        assert task.submodules == []
        assert task.sub_branches == {}


# ===================================================================
# TestSubBranchesCli
# ===================================================================

class TestSubBranchesCli:
    """Test CLI --sub-branch parameter."""

    def test_single_sub_branch(self, cf_project):
        """--sub-branch libs/core:feature-a should parse correctly."""
        from claude_flow.cli import main

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=cf_project), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "add", "Test Sub Branch", "-p", "test prompt",
                "-s", "libs/core",
                "--sub-branch", "libs/core:feature-a",
            ])
            assert result.exit_code == 0
            assert "Added" in result.output

        tm = TaskManager(cf_project)
        tasks = tm.list_tasks()
        assert len(tasks) == 1
        assert "libs/core" in tasks[0].submodules
        assert tasks[0].sub_branches == {"libs/core": "feature-a"}

    def test_multiple_sub_branches(self, cf_project):
        """Multiple --sub-branch args should all be captured."""
        from claude_flow.cli import main

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=cf_project), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "add", "Multi Branch", "-p", "prompt",
                "-s", "libs/core", "-s", "libs/ui", "-s", "apps/server",
                "--sub-branch", "libs/core:feature-auth",
                "--sub-branch", "libs/ui:develop",
            ])
            assert result.exit_code == 0

        tm = TaskManager(cf_project)
        tasks = tm.list_tasks()
        assert set(tasks[0].submodules) == {"libs/core", "libs/ui", "apps/server"}
        assert tasks[0].sub_branches["libs/core"] == "feature-auth"
        assert tasks[0].sub_branches["libs/ui"] == "develop"

    def test_invalid_format_error(self, cf_project):
        """--sub-branch without colon separator should error."""
        from claude_flow.cli import main

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=cf_project), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "add", "Invalid", "-p", "prompt",
                "--sub-branch", "libs/core-no-colon",
            ])
            # Should fail due to invalid format
            assert result.exit_code != 0

    def test_mini_task_support(self, cf_project):
        """Mini task should also support --sub-branch."""
        from claude_flow.cli import main

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=cf_project), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "mini", "fix something",
                "-s", "libs/core",
                "--sub-branch", "libs/core:hotfix",
            ])
            assert result.exit_code == 0

        tm = TaskManager(cf_project)
        tasks = tm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].submodules == ["libs/core"]
        assert tasks[0].sub_branches == {"libs/core": "hotfix"}
        assert tasks[0].is_mini
