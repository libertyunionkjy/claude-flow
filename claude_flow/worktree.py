from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List


class WorktreeManager:
    def __init__(self, repo_root: Path, worktree_dir: Path):
        self._repo = repo_root
        self._wt_dir = worktree_dir

    def _run(self, args: List[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, cwd=cwd or self._repo,
            capture_output=True, text=True, check=check,
        )

    def create(self, task_id: str, branch: str) -> Path:
        wt_path = self._wt_dir / task_id
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["git", "worktree", "add", "-b", branch, str(wt_path)])
        return wt_path

    def remove(self, task_id: str, branch: str) -> None:
        wt_path = self._wt_dir / task_id
        self._run(["git", "worktree", "remove", str(wt_path), "--force"], check=False)
        self._run(["git", "branch", "-D", branch], check=False)

    def merge(self, branch: str, main_branch: str, strategy: str = "--no-ff") -> bool:
        try:
            self._run(["git", "checkout", main_branch])
            self._run(["git", "merge", strategy, branch, "-m", f"merge {branch}"])
            return True
        except subprocess.CalledProcessError:
            self._run(["git", "merge", "--abort"], check=False)
            self._run(["git", "checkout", main_branch], check=False)
            return False

    def list_active(self) -> List[str]:
        if not self._wt_dir.exists():
            return []
        return [d.name for d in self._wt_dir.iterdir() if d.is_dir()]

    def cleanup_all(self) -> int:
        count = 0
        for task_id in self.list_active():
            branch = f"cf/{task_id}"
            self.remove(task_id, branch)
            count += 1
        return count
