import json
from datetime import datetime
from claude_flow.models import Task, TaskStatus


class TestTaskStatus:
    def test_status_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.PLANNING.value == "planning"
        assert TaskStatus.PLANNED.value == "planned"
        assert TaskStatus.APPROVED.value == "approved"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.MERGING.value == "merging"
        assert TaskStatus.DONE.value == "done"
        assert TaskStatus.FAILED.value == "failed"


class TestTask:
    def test_create_task(self):
        task = Task(title="Test task", prompt="Do something")
        assert task.title == "Test task"
        assert task.prompt == "Do something"
        assert task.status == TaskStatus.PENDING
        assert task.id.startswith("task-")
        assert task.branch is None
        assert task.worker_id is None
        assert task.error is None
        assert isinstance(task.created_at, datetime)

    def test_task_to_dict(self):
        task = Task(title="Test", prompt="Prompt")
        d = task.to_dict()
        assert d["title"] == "Test"
        assert d["prompt"] == "Prompt"
        assert d["status"] == "pending"
        assert "created_at" in d

    def test_task_from_dict(self):
        now = datetime.now()
        d = {
            "id": "task-001",
            "title": "Test",
            "prompt": "Prompt",
            "status": "pending",
            "branch": None,
            "plan_file": None,
            "worker_id": None,
            "created_at": now.isoformat(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        task = Task.from_dict(d)
        assert task.id == "task-001"
        assert task.status == TaskStatus.PENDING

    def test_task_roundtrip(self):
        task = Task(title="Roundtrip", prompt="Test prompt")
        d = task.to_dict()
        restored = Task.from_dict(d)
        assert restored.id == task.id
        assert restored.title == task.title
        assert restored.status == task.status

    def test_task_auto_id_increments(self):
        t1 = Task(title="A", prompt="a")
        t2 = Task(title="B", prompt="b")
        assert t1.id != t2.id

    def test_task_branch_name(self):
        task = Task(title="Test", prompt="P")
        task.branch = f"cf/{task.id}"
        assert task.branch.startswith("cf/task-")
