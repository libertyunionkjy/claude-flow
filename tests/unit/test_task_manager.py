import json
from pathlib import Path
from claude_flow.task_manager import TaskManager
from claude_flow.models import Task, TaskStatus


class TestTaskManager:
    def _make_manager(self, tmp_path: Path) -> TaskManager:
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        return TaskManager(tmp_path)

    def test_add_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add("Login API", "Implement login endpoint")
        assert task.title == "Login API"
        assert task.status == TaskStatus.PENDING
        tasks = mgr.list_tasks()
        assert len(tasks) == 1

    def test_list_empty(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.list_tasks() == []

    def test_get_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add("Test", "prompt")
        found = mgr.get(task.id)
        assert found is not None
        assert found.id == task.id

    def test_get_missing(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.get("nonexistent") is None

    def test_remove_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add("Test", "prompt")
        removed = mgr.remove(task.id)
        assert removed is not None
        assert removed.id == task.id
        assert removed.title == "Test"
        assert mgr.list_tasks() == []

    def test_remove_task_returns_none_for_missing(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.remove("nonexistent") is None

    def test_update_status(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add("Test", "prompt")
        mgr.update_status(task.id, TaskStatus.APPROVED)
        updated = mgr.get(task.id)
        assert updated.status == TaskStatus.APPROVED

    def test_claim_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.add("T1", "p1")
        t2 = mgr.add("T2", "p2")
        mgr.update_status(t2.id, TaskStatus.APPROVED)
        claimed = mgr.claim_next(worker_id=0)
        assert claimed is not None
        assert claimed.id == t2.id
        assert claimed.status == TaskStatus.RUNNING
        assert claimed.worker_id == 0

    def test_claim_returns_none_when_empty(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.claim_next(worker_id=0) is None

    def test_add_from_file(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        tasks_file = tmp_path / "tasks.txt"
        tasks_file.write_text("Login | Implement login\nSignup | Implement signup\n")
        added = mgr.add_from_file(tasks_file)
        assert len(added) == 2
        assert added[0].title == "Login"
        assert added[1].prompt == "Implement signup"

    def test_persistence(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.add("Persist", "test persistence")
        mgr2 = TaskManager(tmp_path)
        assert len(mgr2.list_tasks()) == 1
