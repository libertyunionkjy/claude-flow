import subprocess
from pathlib import Path
from claude_flow.worktree import WorktreeManager


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
