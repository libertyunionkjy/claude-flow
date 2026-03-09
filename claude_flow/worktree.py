from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from .utils import can_skip_permissions

if TYPE_CHECKING:
    from claude_flow.config import Config

logger = logging.getLogger(__name__)

# Default timeout for network operations (fetch/push) in seconds
NETWORK_TIMEOUT = 30


class WorktreeManager:
    def __init__(self, repo_root: Path, worktree_dir: Path):
        self._repo = repo_root
        self._wt_dir = worktree_dir

    def _run(self, args: List[str], cwd: Path | None = None, check: bool = True,
             timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                args, cwd=cwd or self._repo,
                capture_output=True, text=True, check=check,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            cmd_str = " ".join(args)
            logger.warning(f"Command timed out after {timeout}s: {cmd_str}")
            # Return a failed CompletedProcess so callers with check=False still work
            return subprocess.CompletedProcess(args, returncode=124, stdout="", stderr=f"Timeout after {timeout}s")

    # ------------------------------------------------------------------
    # Symlink 共享文件
    # ------------------------------------------------------------------

    def _setup_symlinks(self, wt_path: Path, shared: List[str], forbidden: List[str]) -> None:
        """在 worktree 中为共享文件创建 symlink，指向主仓库对应文件。

        - shared 中列出的文件会被 symlink 到主仓库
        - forbidden 中列出的文件会被跳过
        - 主仓库中不存在的文件静默跳过，不报错
        - 同时在 worktree 中创建隔离的 data/ 目录
        """
        # 创建隔离的 data/ 目录
        data_dir = wt_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        for filename in shared:
            # 跳过 forbidden 列表中的文件
            if filename in forbidden:
                continue

            source = self._repo / filename
            target = wt_path / filename

            # 主仓库中不存在的文件静默跳过
            if not source.exists():
                continue

            # 如果目标已存在（可能是 worktree 拷贝的），先删除
            if target.exists() or target.is_symlink():
                target.unlink()

            # 确保目标文件的父目录存在
            target.parent.mkdir(parents=True, exist_ok=True)

            # 创建 symlink，指向主仓库的绝对路径
            target.symlink_to(source.resolve())

    # ------------------------------------------------------------------
    # 创建 worktree
    # ------------------------------------------------------------------

    def create(self, task_id: str, branch: str, config: Config = None) -> Path:
        """创建 worktree 并设置 symlink 共享文件。"""
        wt_path = self._wt_dir / task_id
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["git", "worktree", "add", "-b", branch, str(wt_path)])

        # 如果提供了 config，设置 symlink
        if config is not None:
            self._setup_symlinks(
                wt_path,
                shared=config.shared_symlinks,
                forbidden=config.forbidden_symlinks,
            )

        return wt_path

    # ------------------------------------------------------------------
    # 移除 worktree
    # ------------------------------------------------------------------

    def remove(self, task_id: str, branch: str) -> None:
        wt_path = self._wt_dir / task_id
        self._run(["git", "worktree", "remove", str(wt_path), "--force"], check=False)
        self._run(["git", "branch", "-D", branch], check=False)

    # ------------------------------------------------------------------
    # 原有合并方法（向后兼容）
    # ------------------------------------------------------------------

    def merge(self, branch: str, main_branch: str, strategy: str = "--no-ff") -> bool:
        try:
            self._run(["git", "checkout", main_branch])
            self._run(["git", "merge", strategy, branch, "-m", f"merge {branch}"])
            return True
        except subprocess.CalledProcessError:
            self._run(["git", "merge", "--abort"], check=False)
            self._run(["git", "checkout", main_branch], check=False)
            return False

    # ------------------------------------------------------------------
    # Rebase 合并策略
    # ------------------------------------------------------------------

    def _has_remote(self) -> bool:
        """检查是否配置了 remote origin。"""
        result = self._run(["git", "remote"], check=False)
        return "origin" in result.stdout.split()

    def rebase_and_merge(self, branch: str, main_branch: str, max_retries: int = 5,
                         config: Config = None) -> bool:
        """使用 rebase 策略合并分支。

        流程：
        1. git fetch origin（如果有 remote）
        2. 在 worktree 中执行 git rebase origin/main（或 git rebase main）
        3. 成功后 checkout main，执行 git merge --ff-only
        4. 冲突时使用 claude 解决冲突，然后 git rebase --continue
        5. 最多重试 max_retries 次
        6. 全部失败则 git rebase --abort，返回 False
        """
        has_remote = self._has_remote()

        # 确定 rebase 的目标
        rebase_target = f"origin/{main_branch}" if has_remote else main_branch

        # 找到该 branch 对应的 worktree 路径
        wt_path = self._find_worktree_path(branch)

        # 步骤 1: fetch（如果有 remote，设置超时防止网络挂起）
        if has_remote:
            self._run(["git", "fetch", "origin"], check=False, timeout=NETWORK_TIMEOUT)

        # 步骤 2: 在 worktree 中执行 rebase
        rebase_result = self._run(
            ["git", "rebase", rebase_target],
            cwd=wt_path, check=False,
        )

        if rebase_result.returncode == 0:
            # rebase 成功，执行 ff-only 合并
            return self._ff_merge(branch, main_branch)

        # 步骤 4: 冲突处理 — 最多重试 max_retries 次
        skip_ok = config is not None and can_skip_permissions(
            getattr(config, "skip_permissions", False)
        )

        for _ in range(max_retries):
            if not skip_ok:
                # 没有 skip_permissions 权限（或以 root 运行），无法自动解决冲突
                break

            # 调用 claude 解决冲突
            claude_cmd = ["claude", "-p", "resolve rebase conflict", "--dangerously-skip-permissions"]
            claude_result = self._run(
                claude_cmd,
                cwd=wt_path, check=False,
            )

            if claude_result.returncode != 0:
                break

            # 将所有文件标记为已解决
            self._run(["git", "add", "-A"], cwd=wt_path, check=False)

            # 尝试继续 rebase
            continue_result = self._run(
                ["git", "rebase", "--continue"],
                cwd=wt_path, check=False,
            )

            if continue_result.returncode == 0:
                # rebase 成功，执行 ff-only 合并
                return self._ff_merge(branch, main_branch)

        # 全部失败，abort rebase
        self._run(["git", "rebase", "--abort"], cwd=wt_path, check=False)
        return False

    def _find_worktree_path(self, branch: str) -> Path:
        """根据分支名找到对应的 worktree 路径。

        分支名格式通常为 cf/{task_id}，worktree 路径为 _wt_dir/{task_id}。
        如果找不到对应目录，回退到主仓库路径。
        """
        # 从分支名提取 task_id（分支格式: cf/task-xxxxxx）
        if branch.startswith("cf/"):
            task_id = branch[3:]
            wt_path = self._wt_dir / task_id
            if wt_path.exists():
                return wt_path

        # 回退：遍历 worktree 列表查找
        result = self._run(["git", "worktree", "list", "--porcelain"], check=False)
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                path = Path(line.split(" ", 1)[1])
            elif line.startswith("branch ") and line.endswith(f"/{branch}"):
                return path

        # 最终回退到主仓库
        return self._repo

    def _ff_merge(self, branch: str, main_branch: str) -> bool:
        """checkout 主分支并执行 fast-forward 合并。"""
        try:
            self._run(["git", "checkout", main_branch])
            self._run(["git", "merge", "--ff-only", branch])
            return True
        except subprocess.CalledProcessError:
            self._run(["git", "checkout", main_branch], check=False)
            return False

    # ------------------------------------------------------------------
    # 远程推送支持
    # ------------------------------------------------------------------

    def push(self, main_branch: str) -> bool:
        """将主分支推送到远程仓库。"""
        result = self._run(["git", "push", "origin", main_branch], check=False, timeout=NETWORK_TIMEOUT)
        return result.returncode == 0

    # ------------------------------------------------------------------
    # 列表与清理
    # ------------------------------------------------------------------

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
