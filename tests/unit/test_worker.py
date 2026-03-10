import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from claude_flow.worker import Worker
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import WorktreeManager
from claude_flow.config import Config
from claude_flow.models import TaskStatus


class TestWorker:
    def _setup(self, git_repo: Path):
        cf_dir = git_repo / ".claude-flow"
        cf_dir.mkdir()
        (cf_dir / "logs").mkdir()
        cfg = Config()
        tm = TaskManager(git_repo)
        wt = WorktreeManager(git_repo, cf_dir / "worktrees")
        worker = Worker(worker_id=0, project_root=git_repo, task_manager=tm, worktree_manager=wt, config=cfg)
        return git_repo, tm, wt, worker

    def test_worker_init(self, git_repo):
        _, _, _, worker = self._setup(git_repo)
        assert worker.worker_id == 0

    def test_execute_task_success(self, git_repo):
        repo, tm, wt, worker = self._setup(git_repo)
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(worker_id=0)
        with patch.object(worker, "_run_streaming", return_value=0), \
             patch("claude_flow.worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
            result = worker.execute_task(claimed)
        assert result is True

    def test_execute_task_failure(self, git_repo):
        repo, tm, wt, worker = self._setup(git_repo)
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(worker_id=0)
        with patch.object(worker, "_run_streaming", return_value=1):
            result = worker.execute_task(claimed)
        assert result is False

    def test_run_loop_no_tasks(self, git_repo):
        _, tm, _, worker = self._setup(git_repo)
        count = worker.run_loop()
        assert count == 0

    def test_prompt_includes_worktree_path(self, git_repo):
        """prompt 中应包含 worktree 路径约束和主仓库路径警告。"""
        repo, tm, wt, worker = self._setup(git_repo)
        task = tm.add("Test prompt constraint", "do something")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(worker_id=0)

        captured_cmd = []

        def fake_run_streaming(cmd, *, cwd, env, task, log_file, json_log_file):
            captured_cmd.extend(cmd)
            return 0

        with patch.object(worker, "_run_streaming", side_effect=fake_run_streaming), \
             patch("claude_flow.worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
            worker.execute_task(claimed)

        # prompt 是 cmd[2]（claude -p <prompt>）
        prompt = captured_cmd[2]
        wt_path = repo / ".claude-flow" / "worktrees" / claimed.id
        assert str(wt_path) in prompt
        assert str(repo) in prompt
        assert "禁止操作" in prompt

    def test_repo_contamination_detected(self, git_repo):
        """主仓库有 unstaged 变更时，_check_repo_contamination 应返回文件列表。"""
        repo, tm, wt, worker = self._setup(git_repo)
        # 修改主仓库文件模拟污染
        (repo / "README.md").write_text("contaminated content")
        contaminated = worker._check_repo_contamination()
        assert "README.md" in contaminated

    def test_repo_contamination_rescued(self, git_repo):
        """污染文件应被迁移到 worktree，主仓库应被还原。"""
        repo, tm, wt, worker = self._setup(git_repo)

        # 创建 worktree
        wt_path = wt.create("task-rescue", "cf/task-rescue")

        # 模拟主仓库污染
        original_content = (repo / "README.md").read_text()
        contaminated_content = "contaminated by claude"
        (repo / "README.md").write_text(contaminated_content)

        # 执行迁移
        contaminated = worker._check_repo_contamination()
        assert len(contaminated) > 0
        rescued = worker._rescue_contaminated_changes(wt_path, contaminated)
        assert rescued is True

        # 验证：worktree 中应有污染内容
        assert (wt_path / "README.md").read_text() == contaminated_content

        # 验证：主仓库应被还原
        assert (repo / "README.md").read_text() == original_content

    def test_repo_no_contamination(self, git_repo):
        """主仓库没有变更时，_check_repo_contamination 应返回空列表。"""
        repo, tm, wt, worker = self._setup(git_repo)
        contaminated = worker._check_repo_contamination()
        assert contaminated == []

    def test_strip_worktree_constraint_from_claude_md(self, git_repo):
        """worktree 中 CLAUDE.md 的 worktree 约束段落应被清理。"""
        repo, tm, wt, worker = self._setup(git_repo)
        wt_path = wt.create("task-strip", "cf/task-strip")

        # 模拟 Claude Code 在 CLAUDE.md 末尾追加 worktree 约束
        claude_md = wt_path / "CLAUDE.md"
        original = "# Project\n\nSome content.\n"
        polluted = (
            original + "\n\n"
            "## Worktree 工作目录约束（自动生成）\n\n"
            "你当前工作在一个 Git Worktree 隔离环境中：\n"
            f"- **工作目录**：`{wt_path}`\n"
        )
        claude_md.write_text(polluted, encoding="utf-8")

        worker._strip_worktree_constraint_from_claude_md(wt_path)

        result = claude_md.read_text(encoding="utf-8")
        assert "Worktree 工作目录约束" not in result
        assert "# Project" in result
        assert "Some content." in result

    def test_strip_worktree_constraint_no_marker(self, git_repo):
        """没有 worktree 约束标记的 CLAUDE.md 不应被修改。"""
        repo, tm, wt, worker = self._setup(git_repo)
        wt_path = wt.create("task-clean", "cf/task-clean")

        claude_md = wt_path / "CLAUDE.md"
        original = "# Project\n\nClean content.\n"
        claude_md.write_text(original, encoding="utf-8")

        worker._strip_worktree_constraint_from_claude_md(wt_path)

        assert claude_md.read_text(encoding="utf-8") == original
