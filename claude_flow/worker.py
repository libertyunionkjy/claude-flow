from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config
from .models import Task, TaskStatus
from .task_manager import TaskManager
from .worktree import WorktreeManager

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        worker_id: int,
        project_root: Path,
        task_manager: TaskManager,
        worktree_manager: WorktreeManager,
        config: Config,
    ):
        self.worker_id = worker_id
        self._root = project_root
        self._tm = task_manager
        self._wt = worktree_manager
        self._cfg = config
        self._logs_dir = project_root / ".claude-flow" / "logs"
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        # Worker 专属端口
        self.port = config.base_port + worker_id
        # 守护进程停止标志
        self._stop_requested = False

    def _log_prefix(self) -> str:
        return f"[Worker-{self.worker_id}]"

    def execute_task(self, task: Task) -> bool:
        prefix = self._log_prefix()
        logger.info(f"{prefix} Executing: {task.title} ({task.id})")

        # 创建 worktree（传入 config 自动设置 symlink 共享文件）
        try:
            wt_path = self._wt.create(task.id, task.branch, config=self._cfg)
        except subprocess.CalledProcessError as e:
            self._tm.update_status(task.id, TaskStatus.FAILED, f"Worktree creation failed: {e.stderr}")
            return False

        # 运行 Claude Code
        prompt = f"{self._cfg.task_prompt_prefix}\n\n{task.prompt}"
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if self._cfg.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(self._cfg.claude_args)

        log_file = self._logs_dir / f"{task.id}.log"
        # 设置环境变量（端口分配）
        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["WORKER_ID"] = str(self.worker_id)

        try:
            result = subprocess.run(
                cmd, cwd=str(wt_path),
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True,
                timeout=self._cfg.task_timeout,
                env=env,
            )
            log_file.write_text(result.stdout + "\n" + result.stderr)

            # 解析 stream-json 输出并更新进度
            self._parse_and_update_progress(task, result.stdout)

        except subprocess.TimeoutExpired:
            self._tm.update_status(task.id, TaskStatus.FAILED, "Timeout")
            self._log_progress(task, False, "Timeout", wt_path)
            self._wt.remove(task.id, task.branch)
            return False

        if result.returncode != 0:
            error_msg = f"Exit code {result.returncode}"
            self._tm.update_status(task.id, TaskStatus.FAILED, error_msg)
            self._log_progress(task, False, error_msg, wt_path)
            self._wt.remove(task.id, task.branch)
            return False

        # 自动提交 worktree 中的未提交变更
        has_changes = self._auto_commit(task, wt_path)

        # 检查分支上是否有新 commit（相对于 main）
        if not has_changes and not self._has_new_commits(task.branch, wt_path):
            error_msg = "No code changes produced"
            logger.warning(f"{prefix} {error_msg}")
            self._tm.update_status(task.id, TaskStatus.FAILED, error_msg)
            self._log_progress(task, False, error_msg, wt_path)
            self._wt.remove(task.id, task.branch)
            return False

        # 合并前测试验证
        if self._cfg.pre_merge_commands:
            test_passed = self._run_pre_merge_tests(task, wt_path)
            if not test_passed:
                self._tm.update_status(task.id, TaskStatus.FAILED, "Pre-merge tests failed")
                self._log_progress(task, False, "Pre-merge tests failed", wt_path)
                self._wt.remove(task.id, task.branch)
                return False

        # 合并
        if self._cfg.auto_merge:
            self._tm.update_status(task.id, TaskStatus.MERGING)
            if self._cfg.merge_mode == "rebase":
                success = self._wt.rebase_and_merge(
                    task.branch, self._cfg.main_branch,
                    max_retries=self._cfg.max_merge_retries,
                    config=self._cfg,
                )
            else:
                success = self._wt.merge(task.branch, self._cfg.main_branch, self._cfg.merge_strategy)

            if not success:
                self._tm.update_status(task.id, TaskStatus.FAILED, "CONFLICT")
                self._log_progress(task, False, "Merge conflict", wt_path)
                return False

            # 远程推送
            if self._cfg.auto_push:
                push_ok = self._wt.push(self._cfg.main_branch)
                if not push_ok:
                    logger.warning(f"{prefix} Push failed for {task.id}, task still marked as done")

        # 记录成功经验
        self._log_progress(task, True, None, wt_path)

        # 清理
        self._wt.remove(task.id, task.branch)
        self._tm.update_status(task.id, TaskStatus.DONE)
        logger.info(f"{prefix} Done: {task.title}")
        return True

    def run_loop(self) -> int:
        """一次性执行循环：取完 approved 任务就退出。"""
        completed = 0
        while True:
            task = self._tm.claim_next(self.worker_id)
            if task is None:
                logger.info(f"{self._log_prefix()} No more tasks, exiting")
                break
            success = self.execute_task(task)
            if success:
                completed += 1
        return completed

    def run_daemon(self) -> int:
        """守护进程模式：持续轮询等待新任务，直到收到停止信号。"""
        prefix = self._log_prefix()
        completed = 0
        self._stop_requested = False

        # 注册信号处理器（优雅停止）
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._handle_stop_signal)
        signal.signal(signal.SIGTERM, self._handle_stop_signal)

        logger.info(f"{prefix} Daemon started, polling every {self._cfg.daemon_poll_interval}s")

        try:
            while not self._stop_requested:
                task = self._tm.claim_next(self.worker_id)
                if task is None:
                    # 无任务，等待后重试
                    logger.debug(f"{prefix} No tasks available, sleeping...")
                    time.sleep(self._cfg.daemon_poll_interval)
                    continue

                success = self.execute_task(task)
                if success:
                    completed += 1
                    logger.info(f"{prefix} Completed {completed} tasks so far")
        finally:
            # 恢复原始信号处理器
            signal.signal(signal.SIGINT, original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)

        logger.info(f"{prefix} Daemon stopped, completed {completed} tasks")
        return completed

    def _handle_stop_signal(self, signum: int, frame) -> None:
        """信号处理器：标记停止请求。"""
        logger.info(f"{self._log_prefix()} Received signal {signum}, stopping after current task...")
        self._stop_requested = True

    def _run_pre_merge_tests(self, task: Task, wt_path: Path) -> bool:
        """在 worktree 中执行合并前测试命令。"""
        prefix = self._log_prefix()
        for attempt in range(self._cfg.max_test_retries):
            all_passed = True
            for cmd_str in self._cfg.pre_merge_commands:
                logger.info(f"{prefix} Running test: {cmd_str} (attempt {attempt + 1})")
                result = subprocess.run(
                    cmd_str, shell=True, cwd=str(wt_path),
                    stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=self._cfg.task_timeout,
                )
                if result.returncode != 0:
                    all_passed = False
                    logger.warning(f"{prefix} Test failed: {cmd_str}")
                    # 调用 Claude Code 修复
                    fix_prompt = (
                        f"测试命令 `{cmd_str}` 执行失败，输出如下:\n\n"
                        f"stdout:\n{result.stdout[-2000:]}\n\n"
                        f"stderr:\n{result.stderr[-2000:]}\n\n"
                        f"请修复代码使测试通过。"
                    )
                    fix_cmd = ["claude", "-p", fix_prompt, "--output-format", "stream-json", "--verbose"]
                    if self._cfg.skip_permissions:
                        fix_cmd.append("--dangerously-skip-permissions")
                    subprocess.run(
                        fix_cmd, cwd=str(wt_path),
                        stdin=subprocess.DEVNULL,
                        capture_output=True, text=True,
                        timeout=self._cfg.task_timeout,
                    )
                    break  # 重试整个测试流程

            if all_passed:
                logger.info(f"{prefix} All pre-merge tests passed")
                return True

        logger.error(f"{prefix} Pre-merge tests failed after {self._cfg.max_test_retries} retries")
        return False

    def _parse_and_update_progress(self, task: Task, stdout: str) -> None:
        """解析 stream-json 输出并更新任务进度。"""
        try:
            from .monitor import StreamJsonParser
            parser = StreamJsonParser()
            for line in stdout.splitlines():
                parser.parse_line(line)
            summary = parser.get_summary()
            progress_text = (
                f"Tools: {summary.get('tool_use_count', 0)}, "
                f"Errors: {summary.get('error_count', 0)}"
            )
            self._tm.update_progress(task.id, progress_text)
        except ImportError:
            pass  # monitor 模块不可用时静默跳过
        except Exception:
            pass  # 解析失败不影响主流程

    def _extract_claude_result(self, stdout: str) -> Optional[str]:
        """从 stream-json 输出中提取 Claude 的最终文本回复。"""
        import json as _json
        for line in reversed(stdout.splitlines()):
            try:
                obj = _json.loads(line)
                if obj.get("type") == "result" and obj.get("result"):
                    text = obj["result"]
                    # 截断过长的回复（存入 error 字段）
                    return text[:500] if len(text) > 500 else text
            except (ValueError, KeyError):
                continue
        return None

    def _auto_commit(self, task: Task, wt_path: Path) -> bool:
        """检查 worktree 中是否有未提交的变更，如有则自动提交。

        返回 True 表示有变更并已提交，False 表示无变更。
        """
        prefix = self._log_prefix()
        # 检查是否有未跟踪或已修改的文件
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        if not status_result.stdout.strip():
            return False

        logger.info(f"{prefix} Auto-committing uncommitted changes for {task.id}")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"feat({task.id}): {task.title}",
             "--no-verify"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        return True

    def _has_new_commits(self, branch: str, wt_path: Path) -> bool:
        """检查分支相对于 main 是否有新 commit。"""
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{self._cfg.main_branch}..{branch}"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        try:
            return int(result.stdout.strip()) > 0
        except (ValueError, AttributeError):
            return False

    def _log_progress(self, task: Task, success: bool, error: Optional[str], wt_path: Path) -> None:
        """记录任务经验到 PROGRESS.md。"""
        if not self._cfg.enable_progress_log:
            return
        try:
            from .progress import ProgressLogger
            progress_logger = ProgressLogger(self._root, self._cfg)
            if success:
                commit_id = progress_logger._get_commit_id(wt_path)
                progress_logger.log_success(task, commit_id, wt_path)
            else:
                progress_logger.log_failure(task, error or "Unknown error", wt_path)
        except ImportError:
            pass  # progress 模块不可用时静默跳过
        except Exception as e:
            logger.warning(f"{self._log_prefix()} Progress logging failed: {e}")
