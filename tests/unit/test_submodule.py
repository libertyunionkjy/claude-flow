"""Tests for Git submodule support."""
from datetime import datetime
from claude_flow.models import Task, TaskStatus


class TestTaskSubmodules:
    def test_task_default_submodules_empty(self):
        task = Task(title="Test", prompt="prompt")
        assert task.submodules == []

    def test_task_with_submodules(self):
        task = Task(title="Test", prompt="prompt", submodules=["libs/core", "libs/ui"])
        assert task.submodules == ["libs/core", "libs/ui"]

    def test_task_to_dict_includes_submodules(self):
        task = Task(title="Test", prompt="prompt", submodules=["libs/core"])
        d = task.to_dict()
        assert d["submodules"] == ["libs/core"]

    def test_task_to_dict_empty_submodules(self):
        task = Task(title="Test", prompt="prompt")
        d = task.to_dict()
        assert d["submodules"] == []

    def test_task_from_dict_with_submodules(self):
        d = {
            "id": "task-001", "title": "Test", "prompt": "prompt",
            "status": "pending", "created_at": datetime.now().isoformat(),
            "submodules": ["libs/core", "libs/ui"],
        }
        task = Task.from_dict(d)
        assert task.submodules == ["libs/core", "libs/ui"]

    def test_task_from_dict_without_submodules_backward_compat(self):
        d = {
            "id": "task-001", "title": "Test", "prompt": "prompt",
            "status": "pending", "created_at": datetime.now().isoformat(),
        }
        task = Task.from_dict(d)
        assert task.submodules == []

    def test_task_roundtrip_with_submodules(self):
        task = Task(title="Roundtrip", prompt="p", submodules=["a/b", "c/d"])
        restored = Task.from_dict(task.to_dict())
        assert restored.submodules == ["a/b", "c/d"]


from claude_flow.task_manager import TaskManager


class TestTaskManagerSubmodules:
    def test_add_with_submodules(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add("Test", "prompt", submodules=["libs/core"])
        assert task.submodules == ["libs/core"]
        loaded = tm.get(task.id)
        assert loaded.submodules == ["libs/core"]

    def test_add_mini_with_submodules(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add_mini("Test", "prompt", submodules=["libs/ui"])
        assert task.submodules == ["libs/ui"]
        loaded = tm.get(task.id)
        assert loaded.submodules == ["libs/ui"]

    def test_add_without_submodules_default(self, tmp_path):
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add("Test", "prompt")
        assert task.submodules == []
