from __future__ import annotations

import fcntl
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, TypeVar

from .utils import can_skip_permissions

T = TypeVar("T")

if TYPE_CHECKING:
    from claude_flow.config import Config

logger = logging.getLogger(__name__)

# Default timeout for network operations (fetch/push) in seconds
NETWORK_TIMEOUT = 30


MERGE_LOCK_FILE = "merge.lock"


class WorktreeManager:
    def __init__(self, repo_root: Path, worktree_dir: Path, is_git: bool = True):
        self._repo = repo_root
        self._wt_dir = worktree_dir
        self._merge_lock_file = self._wt_dir / MERGE_LOCK_FILE
        self._is_git = is_git

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

    def _with_merge_lock(self, fn: Callable[[], T]) -> T:
        """在排他文件锁保护下执行 fn，防止多 Worker 同时 merge/rebase。

        使用 fcntl.flock 获取排他锁，与 TaskManager._with_lock 保持一致的模式。
        锁文件位于 worktree_dir/merge.lock。
        """
        self._merge_lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._merge_lock_file, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

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
    # Worktree CLAUDE.md 路径约束注入
    # ------------------------------------------------------------------

    def _inject_worktree_claude_md(self, wt_path: Path, task_id: str) -> None:
        """在 worktree 的 CLAUDE.md 末尾追加工作目录约束指令。

        确保 Claude Code 在 worktree 中启动时能识别正确的项目根路径，
        防止使用主仓库绝对路径操作文件。
        """
        constraint = (
            "\n\n"
            "## Worktree 工作目录约束（自动生成）\n"
            "\n"
            "你当前工作在一个 Git Worktree 隔离环境中：\n"
            f"- **工作目录**：`{wt_path}`\n"
            f"- **任务 ID**：`{task_id}`\n"
            f"- **主仓库**：`{self._repo}`（禁止直接修改）\n"
            "\n"
            "**强制规则**：\n"
            f"1. 所有文件读写操作必须限定在 `{wt_path}` 目录内\n"
            f"2. 禁止使用 `{self._repo}` 的绝对路径操作文件\n"
            f"3. 使用相对路径或以 `{wt_path}` 为前缀的绝对路径\n"
        )

        claude_md = wt_path / "CLAUDE.md"
        if claude_md.exists():
            claude_md.write_text(claude_md.read_text() + constraint)
        else:
            claude_md.write_text(constraint.lstrip())

    # ------------------------------------------------------------------
    # 创建 worktree
    # ------------------------------------------------------------------

    def create(self, task_id: str, branch: str, config: Config = None) -> Path:
        """创建 worktree 并设置 symlink 共享文件。

        Non-git mode: returns the project root directly (no isolation).
        """
        if not self._is_git:
            # Non-git mode: run directly in project root, no isolation
            return self._repo

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

        # 注入工作目录约束到 CLAUDE.md
        self._inject_worktree_claude_md(wt_path, task_id)

        return wt_path

    # ------------------------------------------------------------------
    # 移除 worktree
    # ------------------------------------------------------------------

    def remove(self, task_id: str, branch: str) -> None:
        if not self._is_git:
            return  # Non-git mode: nothing to remove
        wt_path = self._wt_dir / task_id
        self._run(["git", "worktree", "remove", str(wt_path), "--force"], check=False)
        self._run(["git", "branch", "-D", branch], check=False)

    # ------------------------------------------------------------------
    # 原有合并方法（向后兼容）
    # ------------------------------------------------------------------

    def merge(self, branch: str, main_branch: str, strategy: str = "--no-ff",
              config: Config = None,
              task_title: str = "", task_prompt: str = "") -> bool:
        if not self._is_git:
            return True  # Non-git mode: skip merge

        def _do() -> bool:
            try:
                self._run(["git", "checkout", main_branch])
                self._run(["git", "merge", strategy, branch, "-m", f"merge {branch}"])
                return True
            except subprocess.CalledProcessError:
                # 尝试自动解决冲突
                skip_ok = config is not None and can_skip_permissions(
                    getattr(config, "skip_permissions", False)
                )
                max_retries = getattr(config, "max_merge_retries", 3) if config else 3

                if skip_ok:
                    for attempt in range(max_retries):
                        conflict_files = self._get_conflict_files(self._repo)
                        if not conflict_files:
                            break

                        logger.info(f"Merge conflict (attempt {attempt + 1}/{max_retries}), "
                                    f"files: {conflict_files}")

                        prompt = self._build_conflict_prompt(
                            conflict_files,
                            task_title=task_title,
                            task_prompt=task_prompt,
                        )
                        claude_cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
                        claude_result = self._run(claude_cmd, check=False)

                        if claude_result.returncode != 0:
                            break

                        if self._has_conflict_markers(self._repo):
                            continue  # 重试

                        self._run(["git", "add", "-A"], check=False)

                        # 尝试完成 merge commit
                        commit_result = self._run(
                            ["git", "commit", "--no-edit"],
                            check=False,
                        )
                        if commit_result.returncode == 0:
                            return True

                # 解决失败，abort
                self._run(["git", "merge", "--abort"], check=False)
                self._run(["git", "checkout", main_branch], check=False)
                return False
        return self._with_merge_lock(_do)

    # ------------------------------------------------------------------
    # Rebase 合并策略
    # ------------------------------------------------------------------

    def _has_remote(self) -> bool:
        """检查是否配置了 remote origin。"""
        result = self._run(["git", "remote"], check=False)
        return "origin" in result.stdout.split()

    def _get_conflict_files(self, cwd: Path) -> List[str]:
        """获取当前冲突的文件列表。"""
        result = self._run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=cwd, check=False,
        )
        return [f for f in result.stdout.strip().splitlines() if f]

    def _has_conflict_markers(self, cwd: Path) -> bool:
        """检查工作区中是否还残留冲突标记。"""
        result = self._run(
            ["git", "diff", "--check"],
            cwd=cwd, check=False,
        )
        return result.returncode != 0

    def _build_conflict_prompt(self, conflict_files: List[str],
                               task_title: str = "", task_prompt: str = "") -> str:
        """构建包含任务上下文和冲突详情的 prompt。"""
        parts = [
            "你正在解决一个 Git rebase 冲突。",
        ]
        if task_title:
            parts.append(f"任务标题: {task_title}")
        if task_prompt:
            # 截断过长的 prompt
            prompt_text = task_prompt[:800] if len(task_prompt) > 800 else task_prompt
            parts.append(f"任务描述: {prompt_text}")

        parts.append(f"\n以下文件存在冲突，请逐一解决:")
        for f in conflict_files:
            parts.append(f"  - {f}")

        parts.append(
            "\n要求:\n"
            "1. 读取每个冲突文件，理解冲突双方的意图\n"
            "2. 保留双方的有效改动，合理合并\n"
            "3. 删除所有冲突标记 (<<<<<<<, =======, >>>>>>>)\n"
            "4. 确保合并后的代码逻辑正确、可运行"
        )
        return "\n".join(parts)

    def rebase_and_merge(self, branch: str, main_branch: str, max_retries: int = 5,
                         config: Config = None,
                         task_title: str = "", task_prompt: str = "") -> bool:
        """使用 rebase 策略合并分支。

        流程：
        1. git fetch origin（如果有 remote）
        2. 在 worktree 中执行 git rebase origin/main（或 git rebase main）
        3. 成功后 checkout main，执行 git merge --ff-only
        4. 冲突时使用 claude 解决冲突，然后 git rebase --continue
        5. 最多重试 max_retries 次
        6. 全部失败则 git rebase --abort，返回 False
        """
        if not self._is_git:
            return True  # Non-git mode: skip rebase

        def _do() -> bool:
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

            # 步骤 3: 冲突处理 — 最多重试 max_retries 次
            skip_ok = config is not None and can_skip_permissions(
                getattr(config, "skip_permissions", False)
            )

            for attempt in range(max_retries):
                if not skip_ok:
                    logger.warning("Cannot auto-resolve conflicts: skip_permissions not available")
                    break

                # 获取冲突文件列表
                conflict_files = self._get_conflict_files(wt_path)
                if not conflict_files:
                    # 无冲突文件但 rebase 仍失败，尝试直接 continue
                    self._run(["git", "add", "-A"], cwd=wt_path, check=False)
                    continue_result = self._run(
                        ["git", "rebase", "--continue"],
                        cwd=wt_path, check=False,
                    )
                    if continue_result.returncode == 0:
                        return self._ff_merge(branch, main_branch)
                    break

                logger.info(f"Rebase conflict (attempt {attempt + 1}/{max_retries}), "
                            f"files: {conflict_files}")

                # 构建包含任务上下文的冲突解决 prompt
                prompt = self._build_conflict_prompt(
                    conflict_files,
                    task_title=task_title,
                    task_prompt=task_prompt,
                )
                claude_cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
                claude_result = self._run(
                    claude_cmd,
                    cwd=wt_path, check=False,
                )

                if claude_result.returncode != 0:
                    logger.warning(f"Claude conflict resolution failed (exit {claude_result.returncode})")
                    break

                # 验证冲突标记已清除
                if self._has_conflict_markers(wt_path):
                    logger.warning("Conflict markers still present after claude resolution")
                    continue  # 重试

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

        return self._with_merge_lock(_do)

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
        if not self._is_git:
            return False  # Non-git mode: cannot push
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
        if not self._is_git:
            return 0  # Non-git mode: nothing to clean up
        count = 0
        for task_id in self.list_active():
            branch = f"cf/{task_id}"
            self.remove(task_id, branch)
            count += 1
        return count
