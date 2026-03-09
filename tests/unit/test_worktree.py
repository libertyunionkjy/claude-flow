import subprocess
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
from claude_flow.worktree import WorktreeManager, MERGE_LOCK_FILE


class TestWorktreeManager:
    def test_create_worktree(self, git_repo):
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-001", "cf/task-001")
        assert wt_path.exists()
        assert (wt_path / "README.md").exists()

    def test_remove_worktree(self, git_repo):
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-001", "cf/task-001")
        mgr.remove("task-001", "cf/task-001")
        assert not wt_path.exists()

    def test_merge_to_main(self, git_repo):
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-001", "cf/task-001")
        # make a change in worktree
        (wt_path / "new_file.txt").write_text("hello")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "add file"], check=True, capture_output=True)
        success = mgr.merge("cf/task-001", "main")
        assert success is True

    def test_merge_conflict_returns_false(self, git_repo):
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-001", "cf/task-001")
        # make conflicting changes
        (git_repo / "README.md").write_text("# Main change")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "main change"], check=True, capture_output=True)
        (wt_path / "README.md").write_text("# Branch change")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "branch change"], check=True, capture_output=True)
        success = mgr.merge("cf/task-001", "main")
        assert success is False

    def test_list_worktrees(self, git_repo):
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        assert mgr.list_active() == []
        mgr.create("task-001", "cf/task-001")
        active = mgr.list_active()
        assert len(active) == 1
        assert active[0] == "task-001"

    def test_merge_creates_lock_file(self, git_repo):
        """merge 执行后应创建 merge.lock 文件。"""
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-002", "cf/task-002")
        # 在 worktree 中添加文件
        (wt_path / "lock_test.txt").write_text("lock test")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "lock test"], check=True, capture_output=True)
        success = mgr.merge("cf/task-002", "main")
        assert success is True
        assert (wt_dir / MERGE_LOCK_FILE).exists()

    def test_merge_lock_serializes_concurrent_merges(self, git_repo):
        """两个线程并发 merge 时，锁应保证串行执行，均成功完成。"""
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)

        # 创建两个 worktree，分别有不同的文件（无冲突）
        wt1 = mgr.create("task-c01", "cf/task-c01")
        (wt1 / "file_c01.txt").write_text("c01")
        subprocess.run(["git", "-C", str(wt1), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt1), "commit", "-m", "c01"], check=True, capture_output=True)

        wt2 = mgr.create("task-c02", "cf/task-c02")
        (wt2 / "file_c02.txt").write_text("c02")
        subprocess.run(["git", "-C", str(wt2), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt2), "commit", "-m", "c02"], check=True, capture_output=True)

        results = [None, None]

        def merge_task(idx, branch):
            results[idx] = mgr.merge(branch, "main")

        t1 = threading.Thread(target=merge_task, args=(0, "cf/task-c01"))
        t2 = threading.Thread(target=merge_task, args=(1, "cf/task-c02"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # 两个 merge 都应成功（锁保证串行，无竞态）
        assert results[0] is True
        assert results[1] is True

    def test_rebase_and_merge_uses_lock(self, git_repo):
        """rebase_and_merge 应通过 _with_merge_lock 执行。"""
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-003", "cf/task-003")
        (wt_path / "rebase_test.txt").write_text("rebase")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "rebase test"], check=True, capture_output=True)

        success = mgr.rebase_and_merge("cf/task-003", "main")
        assert success is True
        # 锁文件应被创建
        assert (wt_dir / MERGE_LOCK_FILE).exists()

    def test_create_injects_claude_md(self, git_repo):
        """worktree 创建后，CLAUDE.md 应包含工作目录约束指令。"""
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-inject", "cf/task-inject")
        claude_md = wt_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text()
        assert "Worktree 工作目录约束" in content
        assert str(wt_path) in content
        assert str(git_repo) in content
        assert "禁止直接修改" in content

    def test_create_injects_claude_md_when_no_existing(self, git_repo):
        """即使原项目没有 CLAUDE.md，worktree 中也应生成约束文件。"""
        # 删除主仓库的 CLAUDE.md（如果存在）
        main_claude = git_repo / "CLAUDE.md"
        if main_claude.exists():
            main_claude.unlink()
            subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "remove CLAUDE.md"],
                           check=True, capture_output=True)

        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-no-md", "cf/task-no-md")
        claude_md = wt_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text()
        assert "Worktree 工作目录约束" in content
