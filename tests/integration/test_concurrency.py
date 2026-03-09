"""Concurrency tests with 10 parallel workers.

Tests race conditions, lock contention, and data integrity under
concurrent access to TaskManager and WorktreeManager.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import WorktreeManager


CONCURRENCY = 10


class TestConcurrentTaskClaim:
    """Test that claim_next is safe under 10 concurrent workers."""

    def test_no_double_claim(self, cf_project: Path):
        """10 workers claiming simultaneously should never get the same task."""
        tm = TaskManager(cf_project)
        # Create 10 approved tasks
        tasks = []
        for i in range(CONCURRENCY):
            t = tm.add(f"task-{i}", f"prompt-{i}")
            tm.update_status(t.id, TaskStatus.APPROVED)
            tasks.append(t)

        claimed = []
        errors = []

        def worker_claim(worker_id: int):
            try:
                result = tm.claim_next(worker_id)
                if result:
                    claimed.append(result.id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker_claim, args=(i,))
            for i in range(CONCURRENCY)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Errors during claim: {errors}"
        # Each task should be claimed at most once
        assert len(claimed) == len(set(claimed)), (
            f"Double claim detected: {claimed}"
        )
        assert len(claimed) == CONCURRENCY

    def test_claim_more_workers_than_tasks(self, cf_project: Path):
        """10 workers but only 3 tasks -- 7 should get None."""
        tm = TaskManager(cf_project)
        for i in range(3):
            t = tm.add(f"task-{i}", f"prompt-{i}")
            tm.update_status(t.id, TaskStatus.APPROVED)

        claimed = []
        nones = []

        def worker_claim(worker_id: int):
            result = tm.claim_next(worker_id)
            if result:
                claimed.append(result.id)
            else:
                nones.append(worker_id)

        threads = [
            threading.Thread(target=worker_claim, args=(i,))
            for i in range(CONCURRENCY)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(claimed) == 3
        assert len(nones) == 7
        assert len(claimed) == len(set(claimed))


class TestConcurrentReadWrite:
    """Test concurrent reads and writes to task store."""

    def test_concurrent_add_and_list(self, cf_project: Path):
        """10 threads adding tasks while 10 threads listing -- no corruption."""
        tm = TaskManager(cf_project)
        errors = []

        def adder(idx: int):
            try:
                tm.add(f"concurrent-{idx}", f"prompt-{idx}")
            except Exception as e:
                errors.append(("add", idx, e))

        def lister(idx: int):
            try:
                tasks = tm.list_tasks()
                # Should always be a valid list
                assert isinstance(tasks, list)
            except Exception as e:
                errors.append(("list", idx, e))

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = []
            for i in range(CONCURRENCY):
                futures.append(pool.submit(adder, i))
                futures.append(pool.submit(lister, i))
            for f in as_completed(futures):
                f.result()  # Re-raise any exception

        assert not errors, f"Errors: {errors}"
        all_tasks = tm.list_tasks()
        assert len(all_tasks) == CONCURRENCY

    def test_concurrent_status_updates(self, cf_project: Path):
        """10 threads updating different tasks' status simultaneously."""
        tm = TaskManager(cf_project)
        tasks = [tm.add(f"task-{i}", f"prompt-{i}") for i in range(CONCURRENCY)]
        errors = []

        def updater(task: Task):
            try:
                tm.update_status(task.id, TaskStatus.APPROVED)
                tm.update_status(task.id, TaskStatus.RUNNING)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=updater, args=(t,))
            for t in tasks
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        for t in tasks:
            result = tm.get(t.id)
            assert result.status == TaskStatus.RUNNING

    def test_concurrent_priority_updates(self, cf_project: Path):
        """10 threads updating the same task's priority -- last write wins, no crash."""
        tm = TaskManager(cf_project)
        task = tm.add("shared-task", "shared-prompt")
        errors = []

        def update_priority(priority: int):
            try:
                tm.update_priority(task.id, priority)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=update_priority, args=(i,))
            for i in range(CONCURRENCY)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        result = tm.get(task.id)
        assert result.priority in range(CONCURRENCY)  # One of the valid values


class TestConcurrentChatSessions:
    """Test concurrent chat session operations."""

    def test_concurrent_session_create_delete(self, cf_project: Path):
        """10 threads creating and deleting sessions simultaneously."""
        from claude_flow.chat import ChatManager
        config = Config.load(cf_project)
        cm = ChatManager(cf_project, config)
        errors = []

        def session_lifecycle(idx: int):
            try:
                task_id = f"task-chat-{idx}"
                session = cm.create_session(task_id)
                assert session.task_id == task_id
                cm.add_message(task_id, "user", f"Hello {idx}")
                result = cm.get_session(task_id)
                assert result is not None
                assert len(result.messages) >= 1
                cm.delete_session(task_id)
            except Exception as e:
                errors.append((idx, e))

        threads = [
            threading.Thread(target=session_lifecycle, args=(i,))
            for i in range(CONCURRENCY)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Session errors: {errors}"
