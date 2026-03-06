"""Integration tests covering full task lifecycle.

These tests verify that modules (TaskManager, Planner, Worker, WorktreeManager)
work together correctly across the entire task lifecycle, and enforce critical
invariants like subprocess stdin isolation (the root cause of the terminal
corruption bug documented in docs/fix-plan-review-terminal.md).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from claude_flow.config import Config
from claude_flow.models import TaskStatus
from claude_flow.planner import Planner
from claude_flow.task_manager import TaskManager
from claude_flow.worker import Worker
from claude_flow.worktree import WorktreeManager


class TestFullLifecycle:
    """Full lifecycle: task add -> plan -> approve -> execute -> merge -> done."""

    def _build_stack(self, cf_project: Path):
        """Build the complete module stack from an initialized project."""
        cfg = Config.load(cf_project)
        cfg.enable_progress_log = False  # skip PROGRESS.md in tests
        tm = TaskManager(cf_project)
        plans_dir = cf_project / ".claude-flow" / "plans"
        planner = Planner(cf_project, plans_dir, cfg, task_manager=tm)
        wt = WorktreeManager(cf_project, cf_project / cfg.worktree_dir)
        worker = Worker(
            worker_id=0,
            project_root=cf_project,
            task_manager=tm,
            worktree_manager=wt,
            config=cfg,
        )
        return cfg, tm, planner, wt, worker

    def test_full_lifecycle_happy_path(self, cf_project, claude_subprocess_guard):
        """Verify the complete happy path: pending -> done with all transitions."""
        cfg, tm, planner, wt, worker = self._build_stack(cf_project)

        # 1. Add task
        task = tm.add("Implement feature X", "Add a new utility function")
        assert task.status == TaskStatus.PENDING
        assert task.id.startswith("task-")

        # 2. Generate plan
        plan_file = planner.generate(task)
        assert plan_file is not None
        assert plan_file.exists()
        assert "Step one" in plan_file.read_text()

        # Persist PLANNED status (as cli.py does)
        tm.update_status(task.id, TaskStatus.PLANNED)
        refreshed = tm.get(task.id)
        assert refreshed.status == TaskStatus.PLANNED

        # 3. Approve plan
        planner.approve(task)
        tm.update_status(task.id, TaskStatus.APPROVED)
        refreshed = tm.get(task.id)
        assert refreshed.status == TaskStatus.APPROVED

        # 4. Claim and execute
        claimed = tm.claim_next(worker_id=0)
        assert claimed is not None
        assert claimed.status == TaskStatus.RUNNING
        assert claimed.branch == f"cf/{claimed.id}"

        result = worker.execute_task(claimed)
        assert result is True

        # 5. Verify final state
        done_task = tm.get(task.id)
        assert done_task.status == TaskStatus.DONE

        # 6. Verify log file written
        log_file = cf_project / ".claude-flow" / "logs" / f"{task.id}.log"
        assert log_file.exists()

        # 7. CRITICAL: stdin isolation enforced on all claude calls
        claude_subprocess_guard.assert_stdin_isolated()

    def test_plan_generation_failure_and_retry(self, cf_project, claude_subprocess_guard):
        """Plan generation fails, task resets, then succeeds on retry."""
        cfg, tm, planner, wt, worker = self._build_stack(cf_project)

        task = tm.add("Buggy task", "This will fail first")

        # First attempt: simulate failure
        claude_subprocess_guard.set_plan_output("", returncode=1)
        plan_file = planner.generate(task)
        assert plan_file is None
        assert task.status == TaskStatus.FAILED

        # Persist failure
        tm.update_status(task.id, TaskStatus.FAILED, task.error)
        failed_task = tm.get(task.id)
        assert failed_task.status == TaskStatus.FAILED

        # Reset to pending (simulates `cf reset <task_id>`)
        tm.update_status(task.id, TaskStatus.PENDING)
        reset_task = tm.get(task.id)
        assert reset_task.status == TaskStatus.PENDING

        # Second attempt: simulate success
        claude_subprocess_guard.set_plan_output("# Revised Plan\n1. Fixed step")
        # Re-fetch task for generate
        task = tm.get(task.id)
        plan_file = planner.generate(task)
        assert plan_file is not None
        assert plan_file.exists()
        assert "Fixed step" in plan_file.read_text()
        assert task.status == TaskStatus.PLANNED

        # stdin isolation enforced across all attempts
        claude_subprocess_guard.assert_stdin_isolated()

    def test_plan_reject_and_regenerate(self, cf_project, claude_subprocess_guard):
        """Plan rejected with feedback, regenerated, then approved."""
        cfg, tm, planner, wt, worker = self._build_stack(cf_project)

        task = tm.add("Feature Y", "Build feature Y")

        # Generate initial plan
        claude_subprocess_guard.set_plan_output("# Plan v1\nBasic approach")
        plan_file = planner.generate(task)
        assert plan_file is not None
        assert task.status == TaskStatus.PLANNED

        # Reject with reason
        planner.reject(task, "needs error handling")
        assert task.status == TaskStatus.PENDING
        assert "needs error handling" in task.prompt

        # Regenerate (the rejection reason is now in the prompt)
        claude_subprocess_guard.set_plan_output("# Plan v2\nWith error handling")
        plan_file = planner.generate(task)
        assert plan_file is not None
        assert task.status == TaskStatus.PLANNED

        # Approve
        planner.approve(task)
        assert task.status == TaskStatus.APPROVED

        # Verify two plan generation calls were made
        claude_calls = claude_subprocess_guard.get_claude_calls()
        assert len(claude_calls) == 2

        claude_subprocess_guard.assert_stdin_isolated()

    def test_worker_execution_failure(self, cf_project, claude_subprocess_guard):
        """Worker execution fails: task marked FAILED, worktree cleaned up."""
        cfg, tm, planner, wt, worker = self._build_stack(cf_project)

        task = tm.add("Failing task", "This will fail during execution")

        # Plan and approve
        claude_subprocess_guard.set_plan_output("# Plan\n1. Do stuff")
        planner.generate(task)
        planner.approve(task)
        tm.update_status(task.id, TaskStatus.APPROVED)

        # Claim
        claimed = tm.claim_next(worker_id=0)
        assert claimed is not None

        # Execute with failure
        claude_subprocess_guard.set_task_output("", returncode=1)
        result = worker.execute_task(claimed)
        assert result is False

        # Verify failure state
        failed_task = tm.get(task.id)
        assert failed_task.status == TaskStatus.FAILED
        assert failed_task.error is not None

        # Verify worktree cleaned up
        active = wt.list_active()
        assert task.id not in active

        claude_subprocess_guard.assert_stdin_isolated()


class TestStdinIsolation:
    """Regression tests ensuring subprocess stdin isolation.

    This directly guards against the bug in docs/fix-plan-review-terminal.md
    where child processes inherited stdin and corrupted terminal state.
    """

    def test_planner_popen_uses_devnull_stdin(self, cf_project, claude_subprocess_guard):
        """Planner._run_claude() must pass stdin=subprocess.DEVNULL to Popen."""
        cfg = Config.load(cf_project)
        plans_dir = cf_project / ".claude-flow" / "plans"
        planner = Planner(cf_project, plans_dir, cfg)
        from claude_flow.models import Task

        task = Task(title="Test", prompt="test prompt")
        planner.generate(task)

        popen_calls = [c for c in claude_subprocess_guard.calls if "claude" in str(c.args)]
        assert len(popen_calls) >= 1
        for call in popen_calls:
            assert call.kwargs.get("stdin") == subprocess.DEVNULL, (
                f"Planner Popen call missing stdin=DEVNULL: {call.kwargs}"
            )

    def test_worker_run_uses_devnull_stdin(self, cf_project, claude_subprocess_guard):
        """Worker subprocess.run() must pass stdin=subprocess.DEVNULL."""
        cfg = Config.load(cf_project)
        cfg.enable_progress_log = False
        tm = TaskManager(cf_project)
        wt = WorktreeManager(cf_project, cf_project / cfg.worktree_dir)
        worker = Worker(0, cf_project, tm, wt, cfg)

        task = tm.add("Test", "test prompt")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(worker_id=0)

        worker.execute_task(claimed)

        claude_calls = claude_subprocess_guard.get_claude_calls()
        assert len(claude_calls) >= 1
        for call in claude_calls:
            assert call.kwargs.get("stdin") == subprocess.DEVNULL, (
                f"Worker subprocess.run call missing stdin=DEVNULL: {call.kwargs}"
            )

    def test_worker_pre_merge_tests_use_devnull_stdin(self, cf_project, claude_subprocess_guard):
        """Pre-merge test commands must also use stdin=subprocess.DEVNULL."""
        cfg = Config.load(cf_project)
        cfg.enable_progress_log = False
        cfg.pre_merge_commands = ["echo test"]
        tm = TaskManager(cf_project)
        wt = WorktreeManager(cf_project, cf_project / cfg.worktree_dir)
        worker = Worker(0, cf_project, tm, wt, cfg)

        task = tm.add("Test", "test prompt")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(worker_id=0)

        worker.execute_task(claimed)

        # Only claude + shell calls need stdin isolation (not git commands)
        non_git_calls = claude_subprocess_guard.get_non_git_calls()
        assert len(non_git_calls) >= 2, "Expected at least claude call + pre-merge shell call"
        for call in non_git_calls:
            assert call.kwargs.get("stdin") == subprocess.DEVNULL, (
                f"Call missing stdin=DEVNULL: cmd={call.args}, kwargs={call.kwargs}"
            )
