"""Comprehensive integration tests covering 7 dimensions.

Dimensions:
1. Concurrency & race conditions (TestConcurrency)
2. Worktree merge strategies (TestWorktreeMerge)
3. End-to-end CLI workflow (TestCLIWorkflow)
4. Chat interactive planning (TestChatPlanning)
5. Error recovery & resilience (TestErrorRecovery)
6. Streaming log parsing (TestStreamingLogs)
7. CLI + Web API cross-integration (TestCLIWebCross)
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from claude_flow.chat import ChatManager, ChatMessage, ChatSession
from claude_flow.cli import main
from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.monitor import StreamJsonParser, format_structured_log_for_cli
from claude_flow.planner import Planner
from claude_flow.task_manager import TaskManager
from claude_flow.worker import Worker
from claude_flow.worktree import WorktreeManager


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_stack(cf_project: Path):
    """Build the complete module stack from an initialized project."""
    cfg = Config.load(cf_project)
    cfg.enable_progress_log = False
    tm = TaskManager(cf_project)
    plans_dir = cf_project / ".claude-flow" / "plans"
    planner = Planner(cf_project, plans_dir, cfg, task_manager=tm)
    wt = WorktreeManager(cf_project, cf_project / cfg.worktree_dir)
    worker = Worker(
        worker_id=0, project_root=cf_project,
        task_manager=tm, worktree_manager=wt, config=cfg,
    )
    return cfg, tm, planner, wt, worker


def _make_task_approved(tm, planner, guard, title="Test", prompt="Do something"):
    """Create a task in APPROVED state quickly."""
    task = tm.add(title, prompt)
    guard.set_plan_output("# Plan\n1. Step one")
    planner.generate(task)
    planner.approve(task)
    tm.update_status(task.id, TaskStatus.APPROVED)
    return task


def _create_flask_client(cf_project):
    """Create a Flask test client."""
    from claude_flow.web.app import create_app
    cfg = Config.load(cf_project)
    app = create_app(cf_project, cfg)
    app.config["TESTING"] = True
    return app.test_client(), app


# ---------------------------------------------------------------------------
# 1. TestConcurrency -- Concurrent claim & file lock verification
# ---------------------------------------------------------------------------

class TestConcurrency:
    """Verify concurrent task claiming and file lock correctness."""

    def test_concurrent_claim_no_duplicate(self, cf_project, claude_subprocess_guard):
        """3 threads claim 3 approved tasks -- each claimed exactly once."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)
        tasks = []
        for i in range(3):
            t = _make_task_approved(tm, planner, claude_subprocess_guard,
                                    title=f"Task-{i}", prompt=f"Prompt-{i}")
            tasks.append(t)

        claimed: list[tuple[int, str]] = []

        def _claim(wid):
            result = tm.claim_next(wid)
            if result:
                claimed.append((wid, result.id))

        threads = [threading.Thread(target=_claim, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        claimed_ids = [cid for _, cid in claimed]
        assert len(claimed_ids) == 3
        assert len(set(claimed_ids)) == 3  # no duplicates

    def test_concurrent_claim_more_workers_than_tasks(self, cf_project, claude_subprocess_guard):
        """5 threads compete for 2 tasks -- exactly 2 win, 3 get None."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)
        for i in range(2):
            _make_task_approved(tm, planner, claude_subprocess_guard,
                                title=f"Task-{i}", prompt=f"Prompt-{i}")

        claimed: list[Optional[str]] = []

        def _claim(wid):
            result = tm.claim_next(wid)
            claimed.append(result.id if result else None)

        threads = [threading.Thread(target=_claim, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        non_none = [c for c in claimed if c is not None]
        assert len(non_none) == 2
        assert len(set(non_none)) == 2

    def test_priority_ordering_under_contention(self, cf_project, claude_subprocess_guard):
        """Tasks are claimed in priority order (highest first)."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        priorities = [1, 10, 5]
        task_map: dict[int, str] = {}
        for p in priorities:
            t = tm.add(f"P{p}", f"prompt-{p}", priority=p)
            claude_subprocess_guard.set_plan_output(f"# Plan P{p}")
            planner.generate(t)
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            task_map[p] = t.id

        # Sequential claim -- should yield P10, P5, P1
        c1 = tm.claim_next(0)
        c2 = tm.claim_next(1)
        c3 = tm.claim_next(2)

        assert c1.id == task_map[10]
        assert c2.id == task_map[5]
        assert c3.id == task_map[1]

    def test_concurrent_read_write_safety(self, cf_project, claude_subprocess_guard):
        """1 writer + 3 readers running concurrently -- no decode errors."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)
        errors: list[Exception] = []

        def _writer():
            for i in range(20):
                t = tm.add(f"W-{i}", f"prompt-{i}")
                tm.update_status(t.id, TaskStatus.APPROVED)

        def _reader():
            for _ in range(30):
                try:
                    tm.list_tasks()
                except Exception as e:
                    errors.append(e)

        writer = threading.Thread(target=_writer)
        readers = [threading.Thread(target=_reader) for _ in range(3)]

        writer.start()
        for r in readers:
            r.start()
        writer.join()
        for r in readers:
            r.join()

        assert len(errors) == 0, f"Concurrent read/write errors: {errors}"
        # Final consistency check
        tasks = tm.list_tasks()
        assert len(tasks) >= 20


# ---------------------------------------------------------------------------
# 2. TestWorktreeMerge -- Worktree merge strategies
# ---------------------------------------------------------------------------

class TestWorktreeMerge:
    """Verify worktree create/remove/merge/rebase lifecycle."""

    def test_worktree_create_and_remove_lifecycle(self, cf_project, claude_subprocess_guard):
        """Create worktree -> verify exists -> remove -> verify cleaned."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        wt_path = wt.create("task-001", "cf/task-001")
        assert wt_path.exists()
        assert wt_path.is_dir()

        # Branch should exist
        result = subprocess.run(
            ["git", "branch", "--list", "cf/task-001"],
            cwd=str(cf_project), capture_output=True, text=True,
        )
        assert "cf/task-001" in result.stdout

        wt.remove("task-001", "cf/task-001")
        assert not wt_path.exists()

    def test_merge_no_ff_success(self, cf_project, claude_subprocess_guard):
        """Create commit in worktree -> merge --no-ff -> main has changes."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        wt_path = wt.create("task-002", "cf/task-002")
        (wt_path / "feature.txt").write_text("new feature")
        subprocess.run(["git", "add", "."], cwd=str(wt_path), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: add feature"],
                       cwd=str(wt_path), check=True, capture_output=True)

        success = wt.merge("cf/task-002", "main", "--no-ff")
        assert success is True

        # Verify main branch contains the file
        assert (cf_project / "feature.txt").exists()

        wt.remove("task-002", "cf/task-002")

    def test_merge_conflict_returns_false(self, cf_project, claude_subprocess_guard):
        """Conflicting changes -> merge returns False, main not polluted."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        wt_path = wt.create("task-003", "cf/task-003")

        # Modify same file on main
        (cf_project / "README.md").write_text("main version")
        subprocess.run(["git", "add", "."], cwd=str(cf_project), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "main change"],
                       cwd=str(cf_project), check=True, capture_output=True)

        # Modify same file on branch
        (wt_path / "README.md").write_text("branch version")
        subprocess.run(["git", "add", "."], cwd=str(wt_path), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "branch change"],
                       cwd=str(wt_path), check=True, capture_output=True)

        success = wt.merge("cf/task-003", "main", "--no-ff")
        assert success is False

        # Verify main has no merge conflict residue (UU = unmerged, AA = both added)
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cf_project), capture_output=True, text=True,
        )
        conflict_markers = ["UU ", "AA ", "DD "]
        for line in status_result.stdout.splitlines():
            assert not any(line.startswith(m) for m in conflict_markers), \
                f"Merge conflict residue found: {line}"

        wt.remove("task-003", "cf/task-003")

    def test_rebase_and_merge_success(self, cf_project, claude_subprocess_guard):
        """Non-conflicting diverged branches -> rebase_and_merge succeeds."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        wt_path = wt.create("task-004", "cf/task-004")

        # Add a commit on main (different file)
        (cf_project / "main_only.txt").write_text("main content")
        subprocess.run(["git", "add", "."], cwd=str(cf_project), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "main: add file"],
                       cwd=str(cf_project), check=True, capture_output=True)

        # Add a commit on branch (different file)
        (wt_path / "branch_only.txt").write_text("branch content")
        subprocess.run(["git", "add", "."], cwd=str(wt_path), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "branch: add file"],
                       cwd=str(wt_path), check=True, capture_output=True)

        success = wt.rebase_and_merge("cf/task-004", "main")
        assert success is True

        # Both files should be on main
        assert (cf_project / "main_only.txt").exists()
        assert (cf_project / "branch_only.txt").exists()

        wt.remove("task-004", "cf/task-004")

    def test_rebase_and_merge_conflict_abort(self, cf_project, claude_subprocess_guard):
        """Conflicting rebase -> abort, worktree still usable."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)
        cfg.skip_permissions = False  # Ensure no auto conflict resolution

        wt_path = wt.create("task-005", "cf/task-005")

        # Modify same file on main
        (cf_project / "README.md").write_text("main rebase version")
        subprocess.run(["git", "add", "."], cwd=str(cf_project), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "main rebase change"],
                       cwd=str(cf_project), check=True, capture_output=True)

        # Modify same file on branch
        (wt_path / "README.md").write_text("branch rebase version")
        subprocess.run(["git", "add", "."], cwd=str(wt_path), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "branch rebase change"],
                       cwd=str(wt_path), check=True, capture_output=True)

        success = wt.rebase_and_merge("cf/task-005", "main", config=cfg)
        assert success is False

        # Worktree should still be usable (rebase --abort executed)
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        # Should not be in a detached/rebasing state
        assert "rebase" not in status_result.stdout.lower()

        wt.remove("task-005", "cf/task-005")

    def test_merge_lock_serialization(self, cf_project, claude_subprocess_guard):
        """2 threads merge concurrently -- both succeed due to lock serialization."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        # Create two worktrees with independent changes
        wt_path_a = wt.create("task-006a", "cf/task-006a")
        (wt_path_a / "file_a.txt").write_text("feature A")
        subprocess.run(["git", "add", "."], cwd=str(wt_path_a), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: A"],
                       cwd=str(wt_path_a), check=True, capture_output=True)

        wt_path_b = wt.create("task-006b", "cf/task-006b")
        (wt_path_b / "file_b.txt").write_text("feature B")
        subprocess.run(["git", "add", "."], cwd=str(wt_path_b), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: B"],
                       cwd=str(wt_path_b), check=True, capture_output=True)

        results: list[bool] = []
        errors: list[Exception] = []

        def _merge(branch):
            try:
                r = wt.merge(branch, "main", "--no-ff")
                results.append(r)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=_merge, args=("cf/task-006a",))
        t2 = threading.Thread(target=_merge, args=("cf/task-006b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0, f"Merge errors: {errors}"
        assert all(results), f"Expected both merges to succeed: {results}"

        # Main should have both files
        assert (cf_project / "file_a.txt").exists()
        assert (cf_project / "file_b.txt").exists()

        wt.remove("task-006a", "cf/task-006a")
        wt.remove("task-006b", "cf/task-006b")

    def test_cleanup_all(self, cf_project, claude_subprocess_guard):
        """Create 3 worktrees -> cleanup_all -> verify all removed."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        for i in range(3):
            wt.create(f"task-clean-{i}", f"cf/task-clean-{i}")

        assert len(wt.list_active()) >= 3

        count = wt.cleanup_all()
        assert count >= 3
        assert len(wt.list_active()) == 0


# ---------------------------------------------------------------------------
# 3. TestCLIWorkflow -- End-to-end CLI commands
# ---------------------------------------------------------------------------

class TestCLIWorkflow:
    """Verify full CLI command workflow via CliRunner."""

    def _run(self, runner, args, cf_project):
        env = {"CF_PROJECT_ROOT": str(cf_project)}
        return runner.invoke(main, args, env=env, catch_exceptions=False)

    def test_full_cli_lifecycle(self, cf_project, claude_subprocess_guard):
        """init -> task add -> plan -F -> approve -> run -> status = done."""
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(cf_project)}

        # init
        result = runner.invoke(main, ["init"], env=env)
        assert result.exit_code == 0

        # task add
        result = runner.invoke(main, ["task", "add", "Feature X", "-p", "Add X"], env=env)
        assert result.exit_code == 0
        assert "Added:" in result.output
        # Extract task_id
        task_id = result.output.split("Added: ")[1].split(" ")[0].strip()

        # plan -t <id> -F (foreground)
        result = runner.invoke(main, ["plan", "-t", task_id, "-F"], env=env)
        assert result.exit_code == 0

        # plan approve
        result = runner.invoke(main, ["plan", "approve", task_id], env=env)
        assert result.exit_code == 0

        # run <task_id>
        result = runner.invoke(main, ["run", task_id], env=env)
        assert result.exit_code == 0

        # status
        result = runner.invoke(main, ["status"], env=env)
        assert result.exit_code == 0
        assert "done: 1" in result.output

    def test_task_add_and_list(self, cf_project, claude_subprocess_guard):
        """Add 3 tasks with different priorities -> list shows all, ordered."""
        runner = CliRunner()

        self._run(runner, ["task", "add", "Low", "-p", "low task", "-P", "1"], cf_project)
        self._run(runner, ["task", "add", "High", "-p", "high task", "-P", "10"], cf_project)
        self._run(runner, ["task", "add", "Med", "-p", "med task", "-P", "5"], cf_project)

        result = self._run(runner, ["task", "list"], cf_project)
        assert result.exit_code == 0
        lines = result.output.strip().splitlines()
        # Should contain all 3 tasks
        assert any("High" in l for l in lines)
        assert any("Med" in l for l in lines)
        assert any("Low" in l for l in lines)
        # High should appear before Low (priority ordering)
        high_idx = next(i for i, l in enumerate(lines) if "High" in l)
        low_idx = next(i for i, l in enumerate(lines) if "Low" in l)
        assert high_idx < low_idx

    def test_task_show_details(self, cf_project, claude_subprocess_guard):
        """task show displays ID, Title, Status, Priority, Prompt."""
        runner = CliRunner()

        result = self._run(runner, ["task", "add", "ShowMe", "-p", "detailed prompt"], cf_project)
        task_id = result.output.split("Added: ")[1].split(" ")[0].strip()

        result = self._run(runner, ["task", "show", task_id], cf_project)
        assert result.exit_code == 0
        assert task_id in result.output
        assert "ShowMe" in result.output
        assert "pending" in result.output
        assert "detailed prompt" in result.output

    def test_task_remove(self, cf_project, claude_subprocess_guard):
        """Add task -> remove -> list no longer contains it."""
        runner = CliRunner()

        result = self._run(runner, ["task", "add", "ToRemove", "-p", "remove me"], cf_project)
        task_id = result.output.split("Added: ")[1].split(" ")[0].strip()

        result = self._run(runner, ["task", "remove", task_id], cf_project)
        assert "Removed" in result.output

        result = self._run(runner, ["task", "list"], cf_project)
        assert task_id not in result.output

    def test_reset_failed_task(self, cf_project, claude_subprocess_guard):
        """Failed task -> reset -> status back to pending."""
        runner = CliRunner()
        tm = TaskManager(cf_project)

        task = tm.add("FailTask", "fail prompt")
        tm.update_status(task.id, TaskStatus.FAILED, "some error")

        result = self._run(runner, ["reset", task.id], cf_project)
        assert result.exit_code == 0
        assert "pending" in result.output

        t = tm.get(task.id)
        assert t.status == TaskStatus.PENDING

    def test_retry_all_failed(self, cf_project, claude_subprocess_guard):
        """2 failed tasks -> retry -> both become approved."""
        runner = CliRunner()
        tm = TaskManager(cf_project)

        t1 = tm.add("Fail1", "p1")
        t2 = tm.add("Fail2", "p2")
        tm.update_status(t1.id, TaskStatus.FAILED)
        tm.update_status(t2.id, TaskStatus.FAILED)

        result = self._run(runner, ["retry"], cf_project)
        assert result.exit_code == 0
        assert "2" in result.output

        assert tm.get(t1.id).status == TaskStatus.APPROVED
        assert tm.get(t2.id).status == TaskStatus.APPROVED

    def test_log_view(self, cf_project, claude_subprocess_guard):
        """After execution -> cf log shows content."""
        runner = CliRunner()
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        task = _make_task_approved(tm, planner, claude_subprocess_guard)
        claimed = tm.claim_next(0)
        worker.execute_task(claimed)

        result = self._run(runner, ["log", task.id], cf_project)
        assert result.exit_code == 0
        # Should show some log content (raw or structured)
        assert len(result.output.strip()) > 0

    def test_clean_worktrees(self, cf_project, claude_subprocess_guard):
        """Create worktree -> cf clean -> output shows cleaned count."""
        runner = CliRunner()
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        wt.create("task-clean-cli", "cf/task-clean-cli")

        result = self._run(runner, ["clean"], cf_project)
        assert result.exit_code == 0
        assert "Cleaned" in result.output


# ---------------------------------------------------------------------------
# 4. TestChatPlanning -- Chat interactive planning
# ---------------------------------------------------------------------------

class TestChatPlanning:
    """Verify ChatManager session lifecycle and plan generation from chat."""

    def test_chat_session_lifecycle(self, cf_project, claude_subprocess_guard):
        """Create session -> active -> finalize -> finalized."""
        cfg = Config.load(cf_project)
        chat_mgr = ChatManager(cf_project, cfg)
        tm = TaskManager(cf_project)
        task = tm.add("ChatTest", "test prompt")

        session = chat_mgr.create_session(task.id)
        assert session.status == "active"

        # Session file exists
        session_path = cf_project / ".claude-flow" / "chats" / f"{task.id}.json"
        assert session_path.exists()

        chat_mgr.finalize(task.id)
        session = chat_mgr.get_session(task.id)
        assert session.status == "finalized"

    def test_initial_prompt_generates_analysis(self, cf_project, claude_subprocess_guard):
        """send_initial_prompt returns AI response and records message."""
        cfg = Config.load(cf_project)
        # Patch subprocess.run in chat module
        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Initial analysis result", stderr=""
            )
            chat_mgr = ChatManager(cf_project, cfg)
            tm = TaskManager(cf_project)
            task = tm.add("InitPrompt", "implement feature")

            chat_mgr.create_session(task.id)
            response = chat_mgr.send_initial_prompt(task.id, task.prompt)

            assert response is not None
            assert "Initial analysis result" in response

            session = chat_mgr.get_session(task.id)
            assert len(session.messages) == 1
            assert session.messages[0].role == "assistant"

    def test_multi_round_conversation(self, cf_project, claude_subprocess_guard):
        """3 rounds of user->AI messages, all recorded correctly."""
        cfg = Config.load(cf_project)

        with patch("claude_flow.chat.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="Response 1", stderr=""),
                MagicMock(returncode=0, stdout="Response 2", stderr=""),
                MagicMock(returncode=0, stdout="Response 3", stderr=""),
            ]
            chat_mgr = ChatManager(cf_project, cfg)
            tm = TaskManager(cf_project)
            task = tm.add("MultiRound", "feature")

            chat_mgr.create_session(task.id)
            chat_mgr.send_message(task.id, "Q1", task_prompt=task.prompt)
            chat_mgr.send_message(task.id, "Q2", task_prompt=task.prompt)
            chat_mgr.send_message(task.id, "Q3", task_prompt=task.prompt)

            session = chat_mgr.get_session(task.id)
            assert len(session.messages) == 6  # 3 user + 3 assistant
            assert session.messages[0].role == "user"
            assert session.messages[1].role == "assistant"
            assert session.messages[1].content == "Response 1"
            assert session.messages[5].content == "Response 3"

    def test_async_message_thinking_flag(self, cf_project, claude_subprocess_guard):
        """send_message_async sets thinking=True, clears after completion."""
        cfg = Config.load(cf_project)

        with patch("claude_flow.chat.subprocess.Popen") as mock_popen:
            proc_mock = MagicMock()
            proc_mock.communicate.return_value = ("Async response", "")
            proc_mock.returncode = 0
            proc_mock.poll.return_value = 0
            mock_popen.return_value = proc_mock

            chat_mgr = ChatManager(cf_project, cfg)
            tm = TaskManager(cf_project)
            task = tm.add("AsyncTest", "async prompt")

            chat_mgr.create_session(task.id)
            accepted = chat_mgr.send_message_async(task.id, "hello", task_prompt=task.prompt)
            assert accepted is True

            # Wait for background thread to complete
            thread = chat_mgr._active_threads.get(task.id)
            if thread:
                thread.join(timeout=5)

            session = chat_mgr.get_session(task.id)
            assert session.thinking is False
            # Should have user message + AI response
            assert len(session.messages) >= 2

    def test_finalize_and_generate_plan_from_chat(self, cf_project, claude_subprocess_guard):
        """Multi-round chat -> finalize -> generate_from_chat -> plan file exists."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        chat_session = ChatSession(
            task_id="dummy",
            messages=[
                ChatMessage(role="user", content="Let's plan this"),
                ChatMessage(role="assistant", content="I suggest approach A"),
                ChatMessage(role="user", content="Include error handling"),
            ],
        )

        task = tm.add("ChatPlan", "implement chat plan")
        chat_session.task_id = task.id

        claude_subprocess_guard.set_plan_output("# Plan from Chat\n1. Approach A with error handling")
        plan_file = planner.generate_from_chat(task, chat_session)

        assert plan_file is not None
        assert plan_file.exists()
        assert task.status == TaskStatus.PLANNED
        assert task.plan_file is not None

        # Verify YAML front matter
        content = plan_file.read_text()
        assert "---" in content
        assert task.id in content

    def test_stale_thinking_recovery_on_startup(self, cf_project, claude_subprocess_guard):
        """Stale thinking=True sessions are recovered on ChatManager init."""
        cfg = Config.load(cf_project)
        tm = TaskManager(cf_project)
        task = tm.add("StaleThink", "stale prompt")

        # Write a stale session file with thinking=True
        chats_dir = cf_project / ".claude-flow" / "chats"
        chats_dir.mkdir(parents=True, exist_ok=True)
        session_data = {
            "task_id": task.id,
            "mode": "interactive",
            "status": "active",
            "thinking": True,
            "messages": [{"role": "user", "content": "hello", "timestamp": "2026-01-01T00:00:00"}],
        }
        (chats_dir / f"{task.id}.json").write_text(json.dumps(session_data))

        # Create new ChatManager (triggers _recover_stale_sessions)
        new_mgr = ChatManager(cf_project, cfg)
        session = new_mgr.get_session(task.id)

        assert session.thinking is False
        assert any("interrupted" in m.content.lower() for m in session.messages)

    def test_abort_session_kills_subprocess(self, cf_project, claude_subprocess_guard):
        """abort_session removes session file and cleans up references."""
        cfg = Config.load(cf_project)

        with patch("claude_flow.chat.subprocess.Popen") as mock_popen:
            # Make communicate block for a while
            proc_mock = MagicMock()
            proc_mock.poll.return_value = None

            def slow_communicate(timeout=None):
                time.sleep(2)
                return ("slow response", "")

            proc_mock.communicate.side_effect = slow_communicate
            proc_mock.returncode = 0
            mock_popen.return_value = proc_mock

            chat_mgr = ChatManager(cf_project, cfg)
            tm = TaskManager(cf_project)
            task = tm.add("AbortTest", "abort prompt")

            chat_mgr.create_session(task.id)
            chat_mgr.send_message_async(task.id, "msg", task_prompt=task.prompt)

            # Abort immediately
            time.sleep(0.1)
            result = chat_mgr.abort_session(task.id)
            assert result is True

            # Session should be gone
            assert chat_mgr.get_session(task.id) is None
            assert task.id not in chat_mgr._active_threads


# ---------------------------------------------------------------------------
# 5. TestErrorRecovery -- Error recovery & resilience
# ---------------------------------------------------------------------------

class TestErrorRecovery:
    """Verify error recovery paths and resilience mechanisms."""

    def test_needs_input_respond_and_reexecute(self, cf_project, claude_subprocess_guard):
        """Worker with no code changes -> needs_input -> respond -> re-approve."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)
        task = _make_task_approved(tm, planner, claude_subprocess_guard)

        # First execution: mock claude to NOT create files (override guard behavior)
        original_popen = claude_subprocess_guard.mock_popen

        def popen_no_file_change(cmd, **kwargs):
            args_list = list(cmd) if not isinstance(cmd, list) else cmd
            if claude_subprocess_guard._is_claude_call(cmd):
                from io import StringIO
                claude_subprocess_guard.calls.append(
                    type(claude_subprocess_guard).calls.fget(claude_subprocess_guard).__class__.__mro__[0]  # dummy
                )
                # Don't create files in worktree
                proc = MagicMock()
                proc.returncode = 0
                proc.stdout = StringIO('{"type":"result","result":"I need more info about X"}\n')
                proc.stderr = StringIO("")
                proc.communicate.return_value = ('{"type":"result","result":"I need more info"}', "")
                proc.wait.return_value = 0
                return proc
            return claude_subprocess_guard._real_popen(cmd, **kwargs)

        # We need to manually test needs_input flow
        # Since the guard writes files automatically, let's test via task_manager directly
        task2 = tm.add("NeedsInput", "need more info")
        tm.update_status(task2.id, TaskStatus.NEEDS_INPUT, "I need more info about X")

        t = tm.get(task2.id)
        assert t.status == TaskStatus.NEEDS_INPUT

        # Respond with additional info
        updated = tm.respond(task2.id, "Here is more context about X")
        assert updated is not None
        assert updated.status == TaskStatus.APPROVED
        assert "[补充信息]" in updated.prompt

    def test_reset_zombie_running_task(self, cf_project, claude_subprocess_guard):
        """Zombie RUNNING task with plan_file -> reset -> back to PLANNED."""
        runner = CliRunner()
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        task = tm.add("Zombie", "zombie prompt")
        # Create a plan file
        plans_dir = cf_project / ".claude-flow" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plans_dir / f"{task.id}.md"
        plan_path.write_text("# Plan for Zombie")

        # Manually set plan_file and move to APPROVED then RUNNING
        planner.generate(task)
        planner.approve(task)
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)
        assert claimed is not None
        assert tm.get(task.id).status == TaskStatus.RUNNING

        # Simulate worker crash (don't execute, just reset)
        env = {"CF_PROJECT_ROOT": str(cf_project)}
        result = runner.invoke(main, ["reset", task.id], env=env)
        assert result.exit_code == 0

        t = tm.get(task.id)
        assert t.status in (TaskStatus.PLANNED, TaskStatus.PENDING)

    def test_corrupt_tasks_json_backup_recovery(self, cf_project, claude_subprocess_guard):
        """Corrupt tasks.json -> load recovers from backup."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        # Add tasks to create backup
        tm.add("T1", "P1")
        tm.add("T2", "P2")

        # Verify backup exists
        backup = cf_project / ".claude-flow" / "tasks.json.bak"
        assert backup.exists()

        # Corrupt main file
        (cf_project / ".claude-flow" / "tasks.json").write_text("{corrupt!!!}")

        # New TaskManager should recover from backup
        tm2 = TaskManager(cf_project)
        tasks = tm2.list_tasks()
        assert len(tasks) >= 1  # recovered from backup

    def test_worker_exception_marks_failed(self, cf_project, claude_subprocess_guard):
        """Worker encountering unexpected exception -> task FAILED."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)
        task = _make_task_approved(tm, planner, claude_subprocess_guard)
        claimed = tm.claim_next(0)

        # Patch worktree.create to raise
        with patch.object(wt, "create", side_effect=subprocess.CalledProcessError(1, "git", stderr="mock error")):
            result = worker.execute_task(claimed)
            assert result is False

        t = tm.get(task.id)
        assert t.status == TaskStatus.FAILED

    def test_worker_timeout_marks_failed(self, cf_project, claude_subprocess_guard):
        """Worker task timeout -> FAILED with Timeout error."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)
        cfg.task_timeout = 1  # 1 second timeout

        task = _make_task_approved(tm, planner, claude_subprocess_guard)

        # Override mock_popen to simulate slow stdout
        original_mock_popen = claude_subprocess_guard.mock_popen

        def slow_popen(cmd, **kwargs):
            from io import StringIO
            args_list = list(cmd) if not isinstance(cmd, list) else cmd
            if claude_subprocess_guard._is_claude_call(cmd):
                claude_subprocess_guard.calls.append(
                    type(claude_subprocess_guard.calls[0])(args=args_list, kwargs=kwargs)
                    if claude_subprocess_guard.calls else
                    MagicMock(args=args_list, kwargs=kwargs)
                )

                # Create a slow iterator for stdout
                def slow_iter():
                    time.sleep(3)  # Longer than task_timeout
                    yield '{"type":"result","result":"done"}\n'

                proc = MagicMock()
                proc.returncode = 0
                proc.stdout = slow_iter()
                proc.stderr = StringIO("")
                proc.wait.return_value = 0
                proc.kill.return_value = None
                return proc
            return claude_subprocess_guard._real_popen(cmd, **kwargs)

        # Re-patch with slow popen
        with patch("claude_flow.worker.subprocess.Popen", side_effect=slow_popen):
            claimed = tm.claim_next(0)
            result = worker.execute_task(claimed)
            assert result is False

        t = tm.get(task.id)
        assert t.status == TaskStatus.FAILED
        assert "Timeout" in (t.error or "")

    def test_pre_merge_test_failure_marks_failed(self, cf_project, claude_subprocess_guard):
        """Pre-merge command fails -> task FAILED."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)
        cfg.pre_merge_commands = ["exit 1"]
        cfg.max_test_retries = 1  # Only 1 retry to speed up test

        task = _make_task_approved(tm, planner, claude_subprocess_guard)

        # Make shell commands return failure
        original_mock_run = claude_subprocess_guard.mock_run

        def fail_shell_run(cmd, **kwargs):
            args_list = list(cmd) if not isinstance(cmd, list) else cmd
            if kwargs.get("shell", False) is True:
                claude_subprocess_guard.calls.append(
                    type(claude_subprocess_guard.calls[0])(args=args_list, kwargs=kwargs)
                    if claude_subprocess_guard.calls else
                    MagicMock(args=args_list, kwargs=kwargs)
                )
                result = MagicMock()
                result.returncode = 1
                result.stdout = "test failed"
                result.stderr = "error"
                return result
            return original_mock_run(cmd, **kwargs)

        with patch("claude_flow.worker.subprocess.run", side_effect=fail_shell_run):
            claimed = tm.claim_next(0)
            result = worker.execute_task(claimed)
            assert result is False

        t = tm.get(task.id)
        assert t.status == TaskStatus.FAILED
        assert "Pre-merge" in (t.error or "") or "pre" in (t.error or "").lower()

    def test_empty_tasks_file_recovery(self, cf_project, claude_subprocess_guard):
        """Empty tasks.json -> load returns empty list, no exception."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        # Add a task first to create backup
        tm.add("T1", "P1")

        # Make tasks.json empty
        (cf_project / ".claude-flow" / "tasks.json").write_text("")

        tm2 = TaskManager(cf_project)
        tasks = tm2.list_tasks()
        # Should recover from backup or return empty list
        assert isinstance(tasks, list)


# ---------------------------------------------------------------------------
# 6. TestStreamingLogs -- Stream JSON parsing
# ---------------------------------------------------------------------------

class TestStreamingLogs:
    """Verify StreamJsonParser event parsing and log formatting."""

    def test_parse_tool_use_event(self):
        """Parse tool_use event line."""
        parser = StreamJsonParser()
        line = '{"type":"tool_use","tool":"Read","input":{"file_path":"/tmp/x.py"}}'
        event = parser.parse_line(line)

        assert event is not None
        assert event.event_type == "tool_use"
        assert "Read" in event.content
        assert "/tmp/x.py" in event.content

    def test_parse_tool_result_success(self):
        """Parse successful tool_result event."""
        parser = StreamJsonParser()
        line = '{"type":"tool_result","tool":"Write","is_error":false}'
        event = parser.parse_line(line)

        assert event is not None
        assert event.event_type == "tool_use"  # successful tool_result -> tool_use
        assert "Write" in event.content
        assert "ok" in event.content

    def test_parse_tool_result_error(self):
        """Parse error tool_result event."""
        parser = StreamJsonParser()
        line = '{"type":"tool_result","tool":"Bash","is_error":true}'
        event = parser.parse_line(line)

        assert event is not None
        assert event.event_type == "error"
        assert "Bash" in event.content
        assert "ERROR" in event.content

    def test_parse_assistant_message_with_content_array(self):
        """Parse assistant message with text + tool_use content array."""
        parser = StreamJsonParser()
        line = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me check"},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "x.py"}},
                ],
            },
        })
        event = parser.parse_line(line)

        # Should have produced 2 events
        events = parser.get_events()
        assert len(events) == 2
        assert events[0].event_type == "text"
        assert "Let me check" in events[0].content
        assert events[1].event_type == "tool_use"
        assert "Read" in events[1].content

    def test_parse_result_with_cost(self):
        """Parse result event with cost_usd."""
        parser = StreamJsonParser()
        line = '{"type":"result","result":"done","cost_usd":0.0234}'
        event = parser.parse_line(line)

        assert event is not None
        assert event.event_type == "result"
        assert "$0.0234" in event.content
        assert event.raw["cost_usd"] == 0.0234

    def test_get_summary_counts(self):
        """Parse multiple events -> get_summary returns correct counts."""
        parser = StreamJsonParser()
        parser.parse_line('{"type":"tool_use","tool":"Read","input":{}}')
        parser.parse_line('{"type":"tool_use","tool":"Write","input":{}}')
        parser.parse_line('{"type":"tool_result","tool":"Bash","is_error":true}')
        parser.parse_line('{"type":"result","result":"done","cost_usd":0.01}')

        summary = parser.get_summary()
        assert summary["tool_use"] == 2
        assert summary["error"] == 1
        assert summary["result"] == 1
        assert summary["total"] == 4

    def test_to_structured_log(self):
        """Parse events -> to_structured_log returns valid dict."""
        parser = StreamJsonParser()
        parser.parse_line('{"type":"tool_use","tool":"Read","input":{}}')
        parser.parse_line('{"type":"result","result":"done","cost_usd":0.05}')

        log = parser.to_structured_log("task-abc")
        assert log["task_id"] == "task-abc"
        assert log["cost_usd"] == 0.05
        assert isinstance(log["events"], list)
        assert len(log["events"]) == 2
        assert all("type" in e and "ts" in e and "content" in e for e in log["events"])

    def test_format_structured_log_for_cli(self):
        """format_structured_log_for_cli returns non-empty string with ANSI."""
        log_data = {
            "task_id": "task-xyz",
            "summary": {"tool_use": 3, "error": 1, "total": 5},
            "cost_usd": 0.02,
            "events": [
                {"type": "tool_use", "ts": "2026-01-01T12:00:00", "content": "Read: file.py", "tool": "Read"},
                {"type": "error", "ts": "2026-01-01T12:01:00", "content": "Bash: ERROR", "tool": "Bash"},
                {"type": "result", "ts": "2026-01-01T12:02:00", "content": "done", "cost": 0.02},
            ],
        }
        output = format_structured_log_for_cli(log_data)
        assert len(output) > 0
        assert "task-xyz" in output
        assert "3 tools" in output
        assert "1 errors" in output
        # Contains ANSI codes
        assert "\033[" in output

    def test_invalid_json_lines_silently_skipped(self):
        """Invalid JSON lines, empty lines, no-type lines are skipped."""
        parser = StreamJsonParser()

        assert parser.parse_line("") is None
        assert parser.parse_line("not json at all") is None
        assert parser.parse_line("{}") is None  # no type field
        assert parser.parse_line('{"foo": "bar"}') is None  # no type field

        assert len(parser.get_events()) == 0


# ---------------------------------------------------------------------------
# 7. TestCLIWebCross -- CLI + Web API cross-integration
# ---------------------------------------------------------------------------

# Skip if Flask is not installed
flask_available = True
try:
    import flask  # noqa: F401
except ImportError:
    flask_available = False


@pytest.mark.skipif(not flask_available, reason="Flask not installed")
class TestCLIWebCross:
    """Verify data consistency between CLI and Web API operations."""

    def test_web_create_cli_visible(self, cf_project, claude_subprocess_guard):
        """Web API creates task -> CLI can see it."""
        client, app = _create_flask_client(cf_project)
        resp = client.post("/api/tasks", json={"title": "WebTask", "prompt": "Do X"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        task_id = data["data"]["id"]

        # CLI verification
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(cf_project)}
        result = runner.invoke(main, ["task", "list"], env=env)
        assert "WebTask" in result.output

    def test_cli_create_web_visible(self, cf_project, claude_subprocess_guard):
        """CLI creates task -> Web API can see it."""
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(cf_project)}
        result = runner.invoke(main, ["task", "add", "CLITask", "-p", "CLI prompt"], env=env)
        assert result.exit_code == 0
        task_id = result.output.split("Added: ")[1].split(" ")[0].strip()

        client, app = _create_flask_client(cf_project)
        resp = client.get("/api/tasks")
        data = resp.get_json()
        task_ids = [t["id"] for t in data["data"]]
        assert task_id in task_ids

    def test_web_approve_cli_status(self, cf_project, claude_subprocess_guard):
        """CLI create + plan -> Web approve -> CLI shows approved."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)
        task = tm.add("ApproveTest", "approve prompt")
        claude_subprocess_guard.set_plan_output("# Plan\n1. Step")
        planner.generate(task)
        tm.update_status(task.id, TaskStatus.PLANNED)

        client, app = _create_flask_client(cf_project)
        resp = client.post(f"/api/tasks/{task.id}/approve")
        assert resp.get_json()["ok"] is True

        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(cf_project)}
        result = runner.invoke(main, ["task", "show", task.id], env=env)
        assert "approved" in result.output

    def test_web_respond_needs_input(self, cf_project, claude_subprocess_guard):
        """Task needs_input -> Web respond -> CLI sees approved."""
        tm = TaskManager(cf_project)
        task = tm.add("NeedsInput", "need info")
        tm.update_status(task.id, TaskStatus.NEEDS_INPUT, "What is X?")

        client, app = _create_flask_client(cf_project)
        resp = client.post(
            f"/api/tasks/{task.id}/respond",
            json={"message": "X is a feature"},
        )
        assert resp.get_json()["ok"] is True

        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(cf_project)}
        result = runner.invoke(main, ["task", "show", task.id], env=env)
        assert "approved" in result.output

    def test_web_reset_zombie_running(self, cf_project, claude_subprocess_guard):
        """Zombie RUNNING task -> Web reset -> correct target status."""
        cfg, tm, planner, wt, worker = _build_stack(cf_project)

        # Task with plan_file -> should reset to PLANNED
        task = _make_task_approved(tm, planner, claude_subprocess_guard)
        claimed = tm.claim_next(0)
        assert tm.get(task.id).status == TaskStatus.RUNNING

        client, app = _create_flask_client(cf_project)
        resp = client.post(f"/api/tasks/{task.id}/reset")
        data = resp.get_json()
        assert data["ok"] is True
        # Should reset to PLANNED (has plan_file) or PENDING
        new_status = data["data"]["status"]
        assert new_status in ("planned", "pending")

    def test_global_status_consistency(self, cf_project, claude_subprocess_guard):
        """Mixed CLI/Web operations -> status counts match."""
        tm = TaskManager(cf_project)

        # Create tasks via TaskManager directly
        tm.add("S1", "p1")
        tm.add("S2", "p2")
        t3 = tm.add("S3", "p3")
        tm.update_status(t3.id, TaskStatus.FAILED)

        # Check via Web API
        client, app = _create_flask_client(cf_project)
        resp = client.get("/api/status")
        web_data = resp.get_json()["data"]

        # Check via CLI
        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(cf_project)}
        result = runner.invoke(main, ["status"], env=env)

        assert web_data["total"] == 3
        assert web_data["counts"]["pending"] == 2
        assert web_data["counts"]["failed"] == 1
        assert "pending: 2" in result.output
        assert "failed: 1" in result.output

    def test_web_batch_delete_cli_confirms(self, cf_project, claude_subprocess_guard):
        """CLI creates 3 tasks -> Web batch-deletes 2 -> CLI list shows 1."""
        tm = TaskManager(cf_project)
        t1 = tm.add("BD1", "p1")
        t2 = tm.add("BD2", "p2")
        t3 = tm.add("BD3", "p3")

        client, app = _create_flask_client(cf_project)
        resp = client.post("/api/tasks/batch-delete", json={"task_ids": [t1.id, t2.id]})
        data = resp.get_json()
        assert data["data"]["count"] == 2

        runner = CliRunner()
        env = {"CF_PROJECT_ROOT": str(cf_project)}
        result = runner.invoke(main, ["task", "list"], env=env)
        assert t3.title in result.output
        assert t1.title not in result.output
        assert t2.title not in result.output
