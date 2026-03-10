"""CLI end-to-end tests.

Tests complete user workflows through the CLI:
- init -> add -> plan -> approve -> run -> status -> log -> clean
- Interactive planning workflow
- Error recovery workflow

Mock version (default): claude CLI mocked
Smoke version (@pytest.mark.smoke): uses real claude CLI
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from claude_flow.cli import main
from claude_flow.models import TaskStatus


class TestCLIE2EWorkflowMocked:
    """Full CLI workflow with mocked claude."""

    def test_full_lifecycle_init_to_done(self, e2e_project: Path):
        """init -> task add -> plan -> approve -> status -> clean."""
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Step 1: init (already done in fixture, but test idempotency)
        result = runner.invoke(main, ["init"], env=env, catch_exceptions=False)
        assert result.exit_code == 0

        # Step 2: add task
        result = runner.invoke(
            main,
            ["task", "add", "Refactor utils", "-p", "Refactor the utils module for clarity"],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "task-" in result.output  # Should print task ID

        # Extract task ID
        match = re.search(r"(task-[a-f0-9]+)", result.output)
        assert match, f"No task ID in output: {result.output}"
        task_id = match.group(1)

        # Step 3: list tasks
        result = runner.invoke(main, ["task", "list"], env=env, catch_exceptions=False)
        assert result.exit_code == 0
        assert "Refactor utils" in result.output

        # Step 4: plan (mocked claude, foreground)
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("# Plan\n\n## Steps\n1. Do thing", "")
        mock_proc.returncode = 0

        with patch("claude_flow.planner.subprocess.Popen", return_value=mock_proc):
            result = runner.invoke(
                main,
                ["plan", "-t", task_id, "-F"],
                env=env,
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        # Step 5: approve
        result = runner.invoke(
            main,
            ["plan", "approve", task_id],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        # Step 6: plan status
        result = runner.invoke(
            main, ["plan", "status"], env=env, catch_exceptions=False
        )
        assert result.exit_code == 0

        # Step 7: status overview
        result = runner.invoke(
            main, ["status"], env=env, catch_exceptions=False
        )
        assert result.exit_code == 0

        # Step 8: clean
        result = runner.invoke(
            main, ["clean"], env=env, catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_task_remove_workflow(self, e2e_project: Path):
        """Add -> remove -> verify removed."""
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Add
        result = runner.invoke(
            main,
            ["task", "add", "To Delete", "-p", "Will be deleted"],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        match = re.search(r"(task-[a-f0-9]+)", result.output)
        task_id = match.group(1)

        # Remove
        result = runner.invoke(
            main,
            ["task", "remove", task_id],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        # Verify removed
        result = runner.invoke(
            main, ["task", "list"], env=env, catch_exceptions=False
        )
        assert task_id not in result.output

    def test_task_remove_cleans_worktree_and_artifacts(self, e2e_project: Path):
        """Add -> create worktree/plan/log/chat artifacts -> remove -> verify all cleaned."""
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Add a task
        result = runner.invoke(
            main,
            ["task", "add", "Cleanup Test", "-p", "Test cleanup on remove"],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        match = re.search(r"(task-[a-f0-9]+)", result.output)
        task_id = match.group(1)
        branch = f"cf/{task_id}"

        # Create worktree and branch
        from claude_flow.worktree import WorktreeManager
        from claude_flow.config import Config
        cfg = Config.load(e2e_project)
        wt = WorktreeManager(e2e_project, e2e_project / cfg.worktree_dir)
        wt_path = wt.create(task_id, branch)
        assert wt_path.exists()

        # Create plan file
        plan_file = e2e_project / ".claude-flow" / "plans" / f"{task_id}.md"
        plan_file.write_text("# Plan\n")

        # Create log files
        log_file = e2e_project / ".claude-flow" / "logs" / f"{task_id}.log"
        log_file.write_text("log content\n")
        json_log = e2e_project / ".claude-flow" / "logs" / f"{task_id}.json"
        json_log.write_text("{}\n")

        # Create chat session
        chats_dir = e2e_project / ".claude-flow" / "chats"
        chats_dir.mkdir(exist_ok=True)
        chat_file = chats_dir / f"{task_id}.json"
        chat_file.write_text("{}\n")

        # Remove the task
        result = runner.invoke(
            main,
            ["task", "remove", task_id],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert f"Removed {task_id}" in result.output
        assert "Cleaned worktree and branch" in result.output

        # Verify: task removed from list
        result = runner.invoke(main, ["task", "list"], env=env, catch_exceptions=False)
        assert task_id not in result.output

        # Verify: worktree directory removed
        assert not wt_path.exists()

        # Verify: plan file removed
        assert not plan_file.exists()

        # Verify: log files removed
        assert not log_file.exists()
        assert not json_log.exists()

        # Verify: chat file removed
        assert not chat_file.exists()

    def test_reset_and_retry_workflow(self, e2e_project: Path):
        """Add -> simulate failure -> reset -> verify."""
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Add
        result = runner.invoke(
            main,
            ["task", "add", "Fail Test", "-p", "Will fail"],
            env=env,
            catch_exceptions=False,
        )
        match = re.search(r"(task-[a-f0-9]+)", result.output)
        task_id = match.group(1)

        # Manually set to FAILED via task manager
        from claude_flow.task_manager import TaskManager
        tm = TaskManager(e2e_project)
        tm.update_status(task_id, TaskStatus.FAILED, error="simulated failure")

        # Reset
        result = runner.invoke(
            main, ["reset", task_id], env=env, catch_exceptions=False
        )
        assert result.exit_code == 0

        # Verify reset to pending
        task = tm.get(task_id)
        assert task.status in (TaskStatus.PENDING, TaskStatus.APPROVED)

    def test_plan_status_shows_progress(self, e2e_project: Path):
        """plan status should show task progress summary."""
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        result = runner.invoke(
            main, ["plan", "status"], env=env, catch_exceptions=False
        )
        assert result.exit_code == 0


@pytest.mark.smoke
class TestCLIE2ESmoke:
    """Real claude CLI tests. Requires claude to be installed and configured.

    Run with: pytest -m smoke
    Skip with: pytest -m "not smoke"
    """

    def test_real_plan_generation(self, e2e_project: Path, real_claude_available):
        """Generate a real plan using claude CLI."""
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Add task
        result = runner.invoke(
            main,
            ["task", "add", "Add docstring", "-p",
             "Add a one-line docstring to the README.md file"],
            env=env,
            catch_exceptions=False,
        )
        match = re.search(r"(task-[a-f0-9]+)", result.output)
        task_id = match.group(1)

        # Plan with real claude (foreground)
        result = runner.invoke(
            main,
            ["plan", "-t", task_id, "-F"],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        # Verify plan file was created
        plans_dir = e2e_project / ".claude-flow" / "plans"
        plan_files = list(plans_dir.glob(f"{task_id}*.md"))
        assert len(plan_files) >= 1, "No plan file generated"
