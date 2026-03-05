import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from claude_flow.worker import Worker
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import WorktreeManager
from claude_flow.config import Config
from claude_flow.models import TaskStatus


def _init_git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True, capture_output=True)
    (path / "README.md").write_text("# Test")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True)
    return path


class TestWorker:
    def _setup(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir()
        logs_dir = cf_dir / "logs"
        logs_dir.mkdir()
        cfg = Config()
        tm = TaskManager(repo)
        wt = WorktreeManager(repo, cf_dir / "worktrees")
        worker = Worker(worker_id=0, project_root=repo, task_manager=tm, worktree_manager=wt, config=cfg)
        return repo, tm, wt, worker

    def test_worker_init(self, tmp_path):
        _, _, _, worker = self._setup(tmp_path)
        assert worker.worker_id == 0

    def test_execute_task_success(self, tmp_path):
        repo, tm, wt, worker = self._setup(tmp_path)
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(worker_id=0)
        with patch("claude_flow.worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
            result = worker.execute_task(claimed)
        assert result is True

    def test_execute_task_failure(self, tmp_path):
        repo, tm, wt, worker = self._setup(tmp_path)
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(worker_id=0)
        with patch("claude_flow.worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = worker.execute_task(claimed)
        assert result is False

    def test_run_loop_no_tasks(self, tmp_path):
        _, tm, _, worker = self._setup(tmp_path)
        # should exit immediately with no approved tasks
        count = worker.run_loop()
        assert count == 0
