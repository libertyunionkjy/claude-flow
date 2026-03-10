import subprocess
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
from claude_flow.worktree import WorktreeManager, MERGE_LOCK_FILE
from claude_flow.config import Config


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

    def test_ff_merge_fallback_on_upstream_change(self, git_repo):
        """ff-only 失败后应降级到 --no-ff merge。

        模拟场景：rebase 成功后，main 分支被其他 worker 修改，
        ff-only 失败，应降级到 --no-ff 完成合并。
        """
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)

        # 创建 worktree 并提交
        wt_path = mgr.create("task-ff1", "cf/task-ff1")
        (wt_path / "feature.txt").write_text("feature content")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "feat: add feature"],
                       check=True, capture_output=True)

        # 在 main 上也提交（不同文件，无冲突但无法 ff-only）
        (git_repo / "other.txt").write_text("other content")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "main: other change"],
                       check=True, capture_output=True)

        # _ff_merge 应降级到 --no-ff 成功
        success = mgr._ff_merge("cf/task-ff1", "main", wt_path=wt_path)
        assert success is True
        # 两个文件都应存在于 main
        assert (git_repo / "feature.txt").exists()
        assert (git_repo / "other.txt").exists()

        mgr.remove("task-ff1", "cf/task-ff1")

    def test_conflict_prompt_includes_diff_content(self, git_repo):
        """冲突 prompt 应包含 diff 内容和提交历史，而非仅文件名。"""
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)

        prompt = mgr._build_conflict_prompt(
            ["file_a.py", "file_b.py"],
            task_title="Test Feature",
            task_prompt="Implement test feature",
            cwd=git_repo,
        )

        # 应包含完整任务上下文（不截断）
        assert "Test Feature" in prompt
        assert "Implement test feature" in prompt
        # 应包含结构化标题
        assert "## 任务标题" in prompt
        assert "## 任务描述" in prompt
        assert "## 冲突文件详情" in prompt
        assert "## 近期提交历史" in prompt
        assert "## 要求" in prompt
        # 应包含文件名
        assert "file_a.py" in prompt
        assert "file_b.py" in prompt

    def test_conflict_prompt_long_task_prompt_not_truncated(self, git_repo):
        """长任务描述不应被截断（旧版截断到 800 字）。"""
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)

        long_prompt = "A" * 2000
        prompt = mgr._build_conflict_prompt(
            ["file.py"],
            task_title="Test",
            task_prompt=long_prompt,
            cwd=git_repo,
        )
        # 完整 2000 字应全部在 prompt 中
        assert long_prompt in prompt

    def test_merge_succeeds_with_dirty_working_tree(self, git_repo):
        """主仓库有未提交改动时，merge 仍应成功（自动 stash/pop）。"""
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-dirty1", "cf/task-dirty1")

        # 在 worktree 中提交修改
        (wt_path / "feature.txt").write_text("feature")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "feat"], check=True, capture_output=True)

        # 在主仓库制造脏文件（未提交的修改）
        (git_repo / "dirty.txt").write_text("uncommitted work")

        # merge 应成功，不因 dirty tree 失败
        success = mgr.merge("cf/task-dirty1", "main")
        assert success is True

        # 脏文件应仍然存在（stash pop 恢复）
        assert (git_repo / "dirty.txt").exists()
        assert (git_repo / "dirty.txt").read_text() == "uncommitted work"

        mgr.remove("task-dirty1", "cf/task-dirty1")

    def test_ff_merge_succeeds_with_dirty_working_tree(self, git_repo):
        """主仓库有未提交改动时，_ff_merge 仍应成功。"""
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-dirty2", "cf/task-dirty2")

        # 在 worktree 中提交修改
        (wt_path / "ff_feature.txt").write_text("ff feature")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "ff feat"], check=True, capture_output=True)

        # 在主仓库制造脏文件
        (git_repo / "dirty_ff.txt").write_text("dirty ff content")

        # _ff_merge 应成功
        success = mgr._ff_merge("cf/task-dirty2", "main", wt_path=wt_path)
        assert success is True

        # 脏文件应恢复
        assert (git_repo / "dirty_ff.txt").exists()
        assert (git_repo / "dirty_ff.txt").read_text() == "dirty ff content"

        mgr.remove("task-dirty2", "cf/task-dirty2")

    def test_rebase_and_merge_succeeds_with_dirty_working_tree(self, git_repo):
        """主仓库有未提交改动时，rebase_and_merge 仍应成功。"""
        wt_dir = git_repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(git_repo, wt_dir)
        wt_path = mgr.create("task-dirty3", "cf/task-dirty3")

        # 在 worktree 中提交修改
        (wt_path / "rebase_feature.txt").write_text("rebase feature")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "rebase feat"], check=True, capture_output=True)

        # 在主仓库制造脏文件
        (git_repo / "dirty_rebase.txt").write_text("dirty rebase content")

        # rebase_and_merge 应成功
        success = mgr.rebase_and_merge("cf/task-dirty3", "main")
        assert success is True

        # 脏文件应恢复
        assert (git_repo / "dirty_rebase.txt").exists()
        assert (git_repo / "dirty_rebase.txt").read_text() == "dirty rebase content"

        mgr.remove("task-dirty3", "cf/task-dirty3")

