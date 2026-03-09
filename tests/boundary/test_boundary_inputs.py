"""Boundary and edge-case input tests.

Covers empty strings, None values, excessively long strings,
special characters in paths and task fields, and invalid enum values.
"""
from __future__ import annotations

import string
from pathlib import Path

import pytest

from claude_flow.models import Task, TaskStatus
from claude_flow.config import Config
from claude_flow.task_manager import TaskManager


class TestTaskBoundaryInputs:
    """Boundary values for Task model fields."""

    def test_empty_title(self, cf_project: Path):
        """Empty string title should still create a task."""
        tm = TaskManager(cf_project)
        task = tm.add("", "some prompt")
        assert task.title == ""
        assert task.id is not None

    def test_empty_prompt(self, cf_project: Path):
        """Empty string prompt should still create a task."""
        tm = TaskManager(cf_project)
        task = tm.add("title", "")
        assert task.prompt == ""

    def test_very_long_title(self, cf_project: Path):
        """10,000 character title should be handled without truncation."""
        tm = TaskManager(cf_project)
        long_title = "A" * 10_000
        task = tm.add(long_title, "prompt")
        assert len(task.title) == 10_000
        # Verify persistence round-trip
        retrieved = tm.get(task.id)
        assert len(retrieved.title) == 10_000

    def test_very_long_prompt(self, cf_project: Path):
        """100,000 character prompt round-trip."""
        tm = TaskManager(cf_project)
        long_prompt = "B" * 100_000
        task = tm.add("title", long_prompt)
        retrieved = tm.get(task.id)
        assert len(retrieved.prompt) == 100_000

    def test_unicode_in_title_and_prompt(self, cf_project: Path):
        """Unicode characters including CJK, emoji, RTL."""
        tm = TaskManager(cf_project)
        title = "测试任务 \U0001f680 \u0645\u0647\u0645\u0629"
        prompt = "\u8fd9\u662f\u4e00\u4e2a\u5305\u542b\u4e2d\u6587\u3001\u65e5\u672c\u8a9e\u3001\ud55c\uad6d\uc5b4\u7684\u63d0\u793a\u8bcd"
        task = tm.add(title, prompt)
        retrieved = tm.get(task.id)
        assert retrieved.title == title
        assert retrieved.prompt == prompt

    def test_special_characters_in_fields(self, cf_project: Path):
        """Newlines, tabs, quotes, backslashes in title/prompt."""
        tm = TaskManager(cf_project)
        title = 'line1\nline2\ttab "quoted" \\back'
        prompt = "prompt with\x00null byte and\rcarriage return"
        task = tm.add(title, prompt)
        retrieved = tm.get(task.id)
        assert retrieved.title == title

    def test_negative_priority(self, cf_project: Path):
        """Negative priority should be accepted."""
        tm = TaskManager(cf_project)
        task = tm.add("task", "prompt", priority=-100)
        assert task.priority == -100

    def test_very_large_priority(self, cf_project: Path):
        """Extremely large priority value."""
        tm = TaskManager(cf_project)
        task = tm.add("task", "prompt", priority=2**31)
        retrieved = tm.get(task.id)
        assert retrieved.priority == 2**31

    def test_priority_ordering_with_mixed_values(self, cf_project: Path):
        """claim_next should respect priority ordering."""
        tm = TaskManager(cf_project)
        low = tm.add("low", "p", priority=-10)
        high = tm.add("high", "p", priority=100)
        mid = tm.add("mid", "p", priority=0)
        for t in [low, high, mid]:
            tm.update_status(t.id, TaskStatus.APPROVED)

        claimed = tm.claim_next(1)
        assert claimed.id == high.id


class TestTaskManagerBoundary:
    """Boundary cases for TaskManager operations."""

    def test_get_nonexistent_task(self, cf_project: Path):
        """Getting a non-existent task returns None."""
        tm = TaskManager(cf_project)
        assert tm.get("nonexistent-id-12345") is None

    def test_remove_nonexistent_task(self, cf_project: Path):
        """Removing a non-existent task returns False."""
        tm = TaskManager(cf_project)
        assert tm.remove("nonexistent-id-12345") is False

    def test_update_status_nonexistent(self, cf_project: Path):
        """Updating status of non-existent task returns None."""
        tm = TaskManager(cf_project)
        result = tm.update_status("nonexistent", TaskStatus.RUNNING)
        assert result is None

    def test_claim_when_no_approved_tasks(self, cf_project: Path):
        """claim_next with no approved tasks returns None."""
        tm = TaskManager(cf_project)
        tm.add("task", "prompt")  # PENDING, not APPROVED
        assert tm.claim_next(1) is None

    def test_claim_skips_non_approved(self, cf_project: Path):
        """claim_next ignores tasks in non-APPROVED states."""
        tm = TaskManager(cf_project)
        t1 = tm.add("pending", "p")
        t2 = tm.add("running", "p")
        tm.update_status(t2.id, TaskStatus.APPROVED)
        tm.update_status(t2.id, TaskStatus.RUNNING)

        t3 = tm.add("approved", "p")
        tm.update_status(t3.id, TaskStatus.APPROVED)

        claimed = tm.claim_next(1)
        assert claimed.id == t3.id

    def test_add_from_empty_file(self, cf_project: Path, tmp_path: Path):
        """Adding from an empty file should return empty list."""
        tm = TaskManager(cf_project)
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        tasks = tm.add_from_file(empty_file)
        assert tasks == []

    def test_add_from_file_with_blank_lines(self, cf_project: Path, tmp_path: Path):
        """Blank lines in task file should be skipped."""
        tm = TaskManager(cf_project)
        task_file = tmp_path / "tasks.txt"
        task_file.write_text("\n\n  task1|prompt1  \n\n  task2|prompt2  \n\n")
        tasks = tm.add_from_file(task_file)
        assert len(tasks) == 2

    def test_respond_only_works_for_needs_input(self, cf_project: Path):
        """respond() should only work for NEEDS_INPUT status."""
        tm = TaskManager(cf_project)
        task = tm.add("task", "prompt")
        # Task is PENDING, not NEEDS_INPUT
        result = tm.respond(task.id, "extra info")
        assert result is None


class TestModelSerialization:
    """Edge cases in Task serialization/deserialization."""

    def test_from_dict_missing_optional_fields(self):
        """from_dict with only required fields."""
        from datetime import datetime
        minimal = {
            "id": "task-abc123",
            "title": "test",
            "prompt": "test prompt",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
        task = Task.from_dict(minimal)
        assert task.id == "task-abc123"
        assert task.branch is None
        assert task.plan_file is None
        assert task.worker_id is None
        assert task.error is None
        assert task.priority == 0
        assert task.retry_count == 0

    def test_from_dict_unknown_status(self):
        """from_dict with an unrecognized status string."""
        from datetime import datetime
        data = {
            "id": "task-abc123",
            "title": "test",
            "prompt": "test prompt",
            "status": "unknown_status_value",
            "created_at": datetime.now().isoformat(),
        }
        # Should raise ValueError from TaskStatus(...)
        with pytest.raises((ValueError, KeyError)):
            Task.from_dict(data)

    def test_roundtrip_all_fields(self):
        """Serialize and deserialize with all fields populated."""
        task = Task(
            title="full task",
            prompt="full prompt",
            status=TaskStatus.RUNNING,
            branch="cf/task-abc",
            plan_file="/tmp/plan.md",
            worker_id=5,
            error="some error",
            priority=42,
            progress="50%",
            retry_count=3,
            plan_mode="interactive",
        )
        d = task.to_dict()
        restored = Task.from_dict(d)
        assert restored.title == task.title
        assert restored.status == task.status
        assert restored.branch == task.branch
        assert restored.worker_id == task.worker_id
        assert restored.error == task.error
        assert restored.priority == task.priority
        assert restored.progress == task.progress
        assert restored.retry_count == task.retry_count
        assert restored.plan_mode == task.plan_mode
