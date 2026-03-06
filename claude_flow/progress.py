from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .models import Task

logger = logging.getLogger(__name__)


class ProgressLogger:
    """PROGRESS.md 经验沉淀日志管理器。

    每次任务完成或失败后，在主仓库的 PROGRESS.md 中记录经验教训，
    包括遇到的问题、解决方案和 git commit ID。
    """

    # PROGRESS.md 初始内容
    _HEADER = "# Progress Log\n\n"

    def __init__(self, repo_root: Path, config: Config):
        """初始化 ProgressLogger。

        Args:
            repo_root: 主仓库根目录
            config: 包含 enable_progress_log, progress_file 等配置
        """
        self._root = repo_root
        self._config = config
        self._progress_file = repo_root / config.progress_file

    def log_success(self, task: Task, commit_id: str, worktree_path: Path) -> None:
        """任务成功后记录经验。

        使用 subprocess 调用 claude CLI 生成经验总结，
        然后写入主仓库的 PROGRESS.md。

        Args:
            task: 已完成的任务对象
            commit_id: 该任务对应的 git commit ID
            worktree_path: 任务执行时的 worktree 路径
        """
        if not self._config.enable_progress_log:
            return

        # 调用 Claude CLI 生成经验总结
        prompt = (
            f"请为以下已完成的任务生成简短的经验总结（3-5行），"
            f"包括：做了什么、遇到的问题、解决方案、经验教训。\n\n"
            f"任务: {task.title}\n"
            f"Prompt: {task.prompt}\n"
            f"Commit: {commit_id}"
        )
        cmd = ["claude", "-p", prompt, "--print", "--output-format", "text"]

        summary = ""
        try:
            result = subprocess.run(
                cmd,
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                summary = result.stdout.strip()
            else:
                logger.warning(
                    f"Claude CLI 生成经验总结失败 (exit {result.returncode}): "
                    f"{result.stderr}"
                )
                summary = f"（自动总结生成失败，任务已成功完成）"
        except subprocess.TimeoutExpired:
            logger.warning("Claude CLI 生成经验总结超时")
            summary = f"（自动总结生成超时，任务已成功完成）"
        except FileNotFoundError:
            logger.warning("claude CLI 不可用，跳过经验总结生成")
            summary = f"（claude CLI 不可用，任务已成功完成）"

        # 构造条目
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = (
            f"## [{now}] {task.id} - {task.title}\n"
            f"**Status**: SUCCESS\n"
            f"**Commit**: {commit_id}\n\n"
            f"{summary}\n\n"
            f"---\n"
        )

        self._append_entry(entry)
        logger.info(f"已记录任务 {task.id} 的成功经验到 {self._config.progress_file}")

    def log_failure(self, task: Task, error: str, worktree_path: Path) -> None:
        """任务失败后记录错误经验。

        Args:
            task: 失败的任务对象
            error: 错误信息
            worktree_path: 任务执行时的 worktree 路径
        """
        if not self._config.enable_progress_log:
            return

        # 尝试获取 commit ID（失败任务可能没有新的 commit）
        commit_id = self._get_commit_id(worktree_path)

        # 构造条目
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = (
            f"## [{now}] {task.id} - {task.title}\n"
            f"**Status**: FAILED\n"
            f"**Commit**: {commit_id}\n\n"
            f"**错误信息**: {error}\n\n"
            f"**任务 Prompt**: {task.prompt}\n\n"
            f"---\n"
        )

        self._append_entry(entry)
        logger.info(f"已记录任务 {task.id} 的失败经验到 {self._config.progress_file}")

    def _get_commit_id(self, worktree_path: Path) -> str:
        """获取 worktree 中最新的 commit ID。

        Args:
            worktree_path: worktree 路径

        Returns:
            短格式的 commit hash，获取失败时返回 "unknown"
        """
        try:
            result = subprocess.run(
                ["git", "-C", str(worktree_path), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return "unknown"

    def _append_entry(self, entry: str) -> None:
        """追加条目到 PROGRESS.md（最新的在最上面）。

        使用 git -C 操作主仓库，确保从 worktree 中也能正确写入。
        文件不存在时自动创建并添加初始头部。

        Args:
            entry: 要追加的 markdown 条目
        """
        # 读取现有内容或创建初始内容
        if self._progress_file.exists():
            existing = self._progress_file.read_text(encoding="utf-8")
        else:
            existing = self._HEADER

        # 在头部（_HEADER）之后、已有条目之前插入新条目
        if existing.startswith(self._HEADER):
            new_content = self._HEADER + entry + "\n" + existing[len(self._HEADER):]
        else:
            # 文件存在但缺少标准头部，直接在顶部插入
            new_content = self._HEADER + entry + "\n" + existing

        self._progress_file.write_text(new_content, encoding="utf-8")

        # 使用 git -C 将 PROGRESS.md 加入主仓库暂存区
        try:
            subprocess.run(
                [
                    "git", "-C", str(self._root),
                    "add", self._config.progress_file,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("git add PROGRESS.md 失败，文件已写入但未暂存")

    def read(self) -> str:
        """读取当前 PROGRESS.md 内容。

        Returns:
            PROGRESS.md 的完整内容，文件不存在时返回空字符串
        """
        if not self._progress_file.exists():
            return ""
        return self._progress_file.read_text(encoding="utf-8")
