"""Tests for non-git directory support.

Validates that Claude Flow degrades gracefully when running in a directory
that is not a git repository.
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from claude_flow.cli import main
from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.task_manager import TaskManager
from claude_flow.utils import is_git_repo
from claude_flow.worker import Worker
from claude_flow.worktree import WorktreeManager


class TestIsGitRepo:
    """Test git repository detection utility."""

    def test_git_repo_detected(self, git_repo):
        assert is_git_repo(git_repo) is True

    def test_non_git_not_detected(self, non_git_dir):
        assert is_git_repo(non_git_dir) is False

    def test_non_git_tmp_path(self, tmp_path):
        plain = tmp_path / "not_a_repo"
        plain.mkdir()
        assert is_git_repo(plain) is False


class TestWorktreeManagerNonGit:
    """Test WorktreeManager behavior in non-git mode."""

    def test_create_returns_repo_root(self, non_git_dir):
        wt_dir = non_git_dir / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(non_git_dir, wt_dir, is_git=False)
        result = mgr.create("task-001", "cf/task-001")
        assert result == non_git_dir

    def test_remove_is_noop(self, non_git_dir):
        wt_dir = non_git_dir / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(non_git_dir, wt_dir, is_git=False)
        # Should not raise
        mgr.remove("task-001", "cf/task-001")

    def test_merge_returns_true(self, non_git_dir):
        wt_dir = non_git_dir / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(non_git_dir, wt_dir, is_git=False)
        assert mgr.merge("cf/task-001", "main") is True

    def test_rebase_and_merge_returns_true(self, non_git_dir):
        wt_dir = non_git_dir / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(non_git_dir, wt_dir, is_git=False)
        assert mgr.rebase_and_merge("cf/task-001", "main") is True

    def test_push_returns_false(self, non_git_dir):
        wt_dir = non_git_dir / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(non_git_dir, wt_dir, is_git=False)
        assert mgr.push("main") is False

    def test_cleanup_all_returns_zero(self, non_git_dir):
        wt_dir = non_git_dir / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(non_git_dir, wt_dir, is_git=False)
        assert mgr.cleanup_all() == 0


class TestWorkerNonGit:
    """Test Worker behavior in non-git mode."""

    def test_execute_task_simple_mode(self, non_git_dir):
        """Worker should execute task without git operations in non-git mode."""
        cfg = Config()
        tm = TaskManager(non_git_dir)
        wt_dir = non_git_dir / ".claude-flow" / "worktrees"
        wt = WorktreeManager(non_git_dir, wt_dir, is_git=False)
        worker = Worker(0, non_git_dir, tm, wt, cfg, is_git=False)

        task = tm.add("test task", "do something")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        # Mock claude subprocess
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = iter(['{"type":"result","result":"done"}\n'])
        mock_proc.stderr = MagicMock(read=MagicMock(return_value=""))
        mock_proc.wait.return_value = 0
        mock_proc.poll.return_value = 0

        with patch("claude_flow.worker.subprocess.Popen", return_value=mock_proc):
            result = worker.execute_task(claimed)

        assert result is True
        updated = tm.get(task.id)
        assert updated.status == TaskStatus.DONE


class TestCliNonGit:
    """Test CLI commands in non-git directory."""

    def test_init_non_git(self, non_git_dir):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=non_git_dir.parent):
            with patch("claude_flow.cli._get_root", return_value=non_git_dir), \
                 patch("claude_flow.cli.is_git_repo", return_value=False):
                result = runner.invoke(main, ["init"])
                assert result.exit_code == 0
                assert "non-git" in result.output

    def test_init_git(self, git_repo):
        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=git_repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, ["init"])
            assert result.exit_code == 0
            assert "git" in result.output

    def test_clean_non_git(self, non_git_dir):
        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=non_git_dir), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, ["clean"])
            assert result.exit_code == 0
            assert "Non-git" in result.output

    def test_task_add_works_non_git(self, non_git_dir):
        """Task management should work in non-git directories."""
        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=non_git_dir), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, ["task", "add", "test", "-p", "test prompt"])
            assert result.exit_code == 0
            assert "Added" in result.output

    def test_task_list_works_non_git(self, non_git_dir):
        """Task listing should work in non-git directories."""
        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=non_git_dir), \
             patch("claude_flow.cli.is_git_repo", return_value=False):
            result = runner.invoke(main, ["task", "list"])
            assert result.exit_code == 0
