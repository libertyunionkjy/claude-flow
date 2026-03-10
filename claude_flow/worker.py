from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config
from .models import Task, TaskStatus
from .task_manager import TaskManager
from .utils import can_skip_permissions
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
        is_git: bool = True,
    ):
        self.worker_id = worker_id
        self._root = project_root
        self._tm = task_manager
        self._wt = worktree_manager
        self._cfg = config
        self._is_git = is_git
        self._logs_dir = project_root / ".claude-flow" / "logs"
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        # Worker 专属端口
        self.port = config.base_port + worker_id
        # 守护进程停止标志
        self._stop_requested = False
        # Current subprocess handle (for abort support)
        self._current_process: Optional[subprocess.Popen] = None
        # Current task ID being executed
        self._current_task_id: Optional[str] = None

    def _log_prefix(self) -> str:
        return f"[Worker-{self.worker_id}]"

    def execute_task(self, task: Task) -> bool:
        prefix = self._log_prefix()
        logger.info(f"{prefix} Executing: {task.title} ({task.id})")
        self._current_task_id = task.id

        try:
            return self._execute_task_inner(task)
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            logger.error(f"{prefix} {error_msg}")
            self._tm.update_status(task.id, TaskStatus.FAILED, error_msg)
            return False
        finally:
            self._current_task_id = None

    def _execute_task_inner(self, task: Task) -> bool:
        prefix = self._log_prefix()

        if self._is_git:
            return self._execute_task_git(task)
        else:
            return self._execute_task_simple(task)

    def _execute_task_simple(self, task: Task) -> bool:
        """Non-git mode: run Claude Code directly in project root without worktree isolation."""
        prefix = self._log_prefix()
        wt_path = self._root

        prompt = f"{self._cfg.task_prompt_prefix}\n\n{task.prompt}"
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if can_skip_permissions(self._cfg.skip_permissions):
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(self._cfg.claude_args)

        log_file = self._logs_dir / f"{task.id}.log"
        json_log_file = self._logs_dir / f"{task.id}.json"
        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["WORKER_ID"] = str(self.worker_id)

        try:
            returncode = self._run_streaming(
                cmd, cwd=str(wt_path), env=env,
                task=task, log_file=log_file, json_log_file=json_log_file,
            )
        except subprocess.TimeoutExpired:
            self._tm.update_status(task.id, TaskStatus.FAILED, "Timeout")
            self._log_progress(task, False, "Timeout", wt_path)
            return False

        if returncode != 0:
            error_msg = f"Exit code {returncode}"
            self._tm.update_status(task.id, TaskStatus.FAILED, error_msg)
            self._log_progress(task, False, error_msg, wt_path)
            return False

        # 合并前测试验证（non-git mode 也支持）
        if self._cfg.pre_merge_commands:
            test_passed = self._run_pre_merge_tests(task, wt_path)
            if not test_passed:
                self._tm.update_status(task.id, TaskStatus.FAILED, "Pre-merge tests failed")
                self._log_progress(task, False, "Pre-merge tests failed", wt_path)
                return False

        self._log_progress(task, True, None, wt_path)
        self._tm.update_status(task.id, TaskStatus.DONE)
        logger.info(f"{prefix} Done (non-git): {task.title}")
        return True

    def _execute_task_git(self, task: Task) -> bool:
        """Git mode: full worktree isolation, commit, merge flow."""
        prefix = self._log_prefix()

        # 创建 worktree（传入 config 自动设置 symlink 共享文件）
        try:
            wt_path = self._wt.create(task.id, task.branch, config=self._cfg)
        except subprocess.CalledProcessError as e:
            self._tm.update_status(task.id, TaskStatus.FAILED, f"Worktree creation failed: {e.stderr}")
            return False

        # 运行 Claude Code（注入 worktree 路径约束）
        worktree_constraint = (
            f"重要：你的项目工作目录是 {wt_path}。"
            f"所有文件操作（读取、编辑、创建）必须在此目录下进行，"
            f"禁止操作 {self._root} 路径下的文件。"
        )
        prompt = f"{self._cfg.task_prompt_prefix}\n\n{worktree_constraint}\n\n{task.prompt}"
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if can_skip_permissions(self._cfg.skip_permissions):
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(self._cfg.claude_args)

        log_file = self._logs_dir / f"{task.id}.log"
        json_log_file = self._logs_dir / f"{task.id}.json"
        # 设置环境变量（端口分配）
        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["WORKER_ID"] = str(self.worker_id)

        try:
            returncode = self._run_streaming(
                cmd, cwd=str(wt_path), env=env,
                task=task, log_file=log_file, json_log_file=json_log_file,
            )
        except subprocess.TimeoutExpired:
            self._tm.update_status(task.id, TaskStatus.FAILED, "Timeout")
            self._log_progress(task, False, "Timeout", wt_path)
            self._wt.remove(task.id, task.branch)
            return False

        if returncode != 0:
            error_msg = f"Exit code {returncode}"
            self._tm.update_status(task.id, TaskStatus.FAILED, error_msg)
            self._log_progress(task, False, error_msg, wt_path)
            self._wt.remove(task.id, task.branch)
            return False

        # 检测主仓库是否被意外修改（Claude 可能使用了主仓库绝对路径）
        contaminated_files = self._check_repo_contamination()
        if contaminated_files:
            self._rescue_contaminated_changes(wt_path, contaminated_files)

        # 清理 Claude Code 可能写入 CLAUDE.md 的 worktree 约束段落
        self._strip_worktree_constraint_from_claude_md(wt_path)

        # 自动提交 worktree 中的未提交变更
        has_changes = self._auto_commit(task, wt_path)

        # 检查分支上是否有新 commit（相对于 main）
        stdout_content = log_file.read_text() if log_file.exists() else ""
        if not has_changes and not self._has_new_commits(task.branch, wt_path):
            claude_reply = self._extract_claude_result(stdout_content)
            error_msg = claude_reply or "No code changes produced"
            logger.warning(f"{prefix} No changes detected, marking as needs_input")
            self._tm.update_status(task.id, TaskStatus.NEEDS_INPUT, error_msg)
            self._log_progress(task, False, "needs_input: " + error_msg, wt_path)
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
                    task_title=task.title,
                    task_prompt=task.prompt,
                    timeout=self._cfg.task_timeout,
                )
            else:
                success = self._wt.merge(
                    task.branch, self._cfg.main_branch, self._cfg.merge_strategy,
                    config=self._cfg,
                    task_title=task.title,
                    task_prompt=task.prompt,
                )

            if not success:
                self._tm.update_status(task.id, TaskStatus.FAILED, "CONFLICT")
                self._log_progress(task, False, "Merge conflict", wt_path)
                return False

            # 合并后测试验证（在 worktree 中验证合并结果的正确性）
            if self._cfg.pre_merge_commands:
                post_merge_ok = self._run_pre_merge_tests(task, wt_path)
                if not post_merge_ok:
                    logger.warning(f"{prefix} Post-merge tests failed for {task.id}, "
                                   "task still marked as done (merge already completed)")

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

    def run_loop(self, worker_registry: Optional[dict] = None) -> int:
        """一次性执行循环：取完 approved 任务就退出。

        Args:
            worker_registry: Optional dict to register self by task_id for abort support.
        """
        completed = 0
        while True:
            task = self._tm.claim_next(self.worker_id)
            if task is None:
                logger.info(f"{self._log_prefix()} No more tasks, exiting")
                break
            if worker_registry is not None:
                worker_registry[task.id] = self
            try:
                success = self.execute_task(task)
            except Exception as e:
                logger.error(f"{self._log_prefix()} Task {task.id} crashed: {e}")
                self._tm.update_status(task.id, TaskStatus.FAILED, f"Worker crash: {e}")
                success = False
            finally:
                if worker_registry is not None:
                    worker_registry.pop(task.id, None)
            if success:
                completed += 1
        return completed

    def run_daemon(self, worker_registry: Optional[dict] = None) -> int:
        """守护进程模式：持续轮询等待新任务，直到收到停止信号。

        Args:
            worker_registry: Optional dict to register self by task_id for abort support.
        """
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

                if worker_registry is not None:
                    worker_registry[task.id] = self
                try:
                    success = self.execute_task(task)
                except Exception as e:
                    logger.error(f"{prefix} Task {task.id} crashed: {e}")
                    self._tm.update_status(task.id, TaskStatus.FAILED, f"Worker crash: {e}")
                    success = False
                finally:
                    if worker_registry is not None:
                        worker_registry.pop(task.id, None)
                if success:
                    completed += 1
                    logger.info(f"{prefix} Completed {completed} tasks so far")
        finally:
            # 恢复原始信号处理器
            signal.signal(signal.SIGINT, original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)

        logger.info(f"{prefix} Daemon stopped, completed {completed} tasks")
        return completed

    def stop(self) -> None:
        """Stop the worker, killing the current subprocess if any.

        Used when deleting a task that is currently being executed.
        """
        self._stop_requested = True
        proc = self._current_process
        if proc and proc.poll() is None:
            logger.info(f"{self._log_prefix()} Killing current subprocess for task abort")
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

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
                    if can_skip_permissions(self._cfg.skip_permissions):
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

    def _run_streaming(
        self,
        cmd: list[str],
        *,
        cwd: str,
        env: dict,
        task: Task,
        log_file: Path,
        json_log_file: Path,
    ) -> int:
        """流式执行子进程，实时写入 raw log 和结构化 JSON 日志。

        使用 Popen 替代 subprocess.run，逐行读取 stdout 并追加写入日志文件，
        使得 RUNNING 状态的任务可以通过 View Log 实时查看进度。

        Returns:
            子进程退出码。

        Raises:
            subprocess.TimeoutExpired: 超时时抛出。
        """
        import json as _json
        from .monitor import StreamJsonParser

        parser = StreamJsonParser()
        start_time = time.time()

        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._current_process = proc

        stderr_lines: list[str] = []
        flush_counter = 0

        try:
            with open(log_file, "w") as lf:
                for line in proc.stdout:
                    # 写入 raw log 文件
                    lf.write(line)
                    flush_counter += 1
                    if flush_counter % 5 == 0:
                        lf.flush()

                    # 解析 stream-json 并增量保存结构化日志
                    parser.parse_line(line)
                    if flush_counter % 10 == 0:
                        self._flush_structured_log(parser, task.id, json_log_file)

                    # 超时检查
                    if time.time() - start_time > self._cfg.task_timeout:
                        proc.kill()
                        proc.wait()
                        raise subprocess.TimeoutExpired(cmd, self._cfg.task_timeout)

                # 读取 stderr
                stderr_output = proc.stderr.read() if proc.stderr else ""
                if stderr_output:
                    lf.write("\n" + stderr_output)
                    lf.flush()

            proc.wait()

            # 最终保存完整的结构化日志
            self._flush_structured_log(parser, task.id, json_log_file)

            # 更新进度摘要
            summary = parser.get_summary()
            progress_text = (
                f"Tools: {summary.get('tool_use', 0)}, "
                f"Errors: {summary.get('error', 0)}"
            )
            self._tm.update_progress(task.id, progress_text)

        except Exception:
            proc.kill()
            proc.wait()
            raise
        finally:
            self._current_process = None

        return proc.returncode

    def _flush_structured_log(self, parser, task_id: str, json_log_file: Path) -> None:
        """将当前解析状态持久化为结构化 JSON 日志文件。"""
        try:
            import json as _json
            structured = parser.to_structured_log(task_id)
            json_log_file.write_text(
                _json.dumps(structured, indent=2, ensure_ascii=False)
            )
        except Exception:
            pass  # 写入失败不影响主流程

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

    # Worktree 约束段落的标记，用于检测和清理
    _WT_CONSTRAINT_MARKER = "## Worktree 工作目录约束"

    def _strip_worktree_constraint_from_claude_md(self, wt_path: Path) -> None:
        """清理 worktree 中所有 CLAUDE.md 文件里被 Claude Code 自行追加的 worktree 约束段落。

        Claude Code 在 worktree 中运行时，可能会读取 prompt 中的 worktree 路径约束，
        然后"贴心地"将其写入 CLAUDE.md。这些内容如果随提交合并到 main 分支，
        会导致 CLAUDE.md 包含指向已删除 worktree 的硬编码路径。
        """
        import re

        # 扫描 worktree 中所有 CLAUDE.md 文件
        claude_md_files = list(wt_path.rglob("CLAUDE.md"))
        for md_file in claude_md_files:
            # 跳过 .claude-flow 子目录中的文件
            try:
                md_file.relative_to(wt_path / ".claude-flow")
                continue
            except ValueError:
                pass

            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            if self._WT_CONSTRAINT_MARKER not in content:
                continue

            # 移除从标记开始到文件末尾的内容（该段落总是追加在最后）
            marker_pos = content.find(self._WT_CONSTRAINT_MARKER)
            # 同时清理标记前的连续空行
            cleaned = content[:marker_pos].rstrip("\n") + "\n"

            if cleaned != content:
                md_file.write_text(cleaned, encoding="utf-8")
                logger.info(f"Stripped worktree constraint from {md_file.relative_to(wt_path)}")

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

    def _check_repo_contamination(self) -> list[str]:
        """检查主仓库是否有被意外修改的文件（unstaged changes）。

        返回被修改的文件相对路径列表（相对于主仓库根）。
        """
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=str(self._root), capture_output=True, text=True,
        )
        return [f for f in result.stdout.strip().splitlines() if f]

    def _rescue_contaminated_changes(self, wt_path: Path, contaminated_files: list[str]) -> bool:
        """将主仓库中被误修改的文件迁移到 worktree，然后还原主仓库。

        Args:
            wt_path: worktree 路径
            contaminated_files: 被污染的文件相对路径列表

        Returns:
            True 表示成功迁移了文件
        """
        prefix = self._log_prefix()
        migrated = 0

        for rel_path in contaminated_files:
            src = self._root / rel_path
            dst = wt_path / rel_path

            if not src.exists():
                continue

            # 确保目标目录存在
            dst.parent.mkdir(parents=True, exist_ok=True)

            try:
                shutil.copy2(str(src), str(dst))
                migrated += 1
                logger.info(f"{prefix} Rescued contaminated file: {rel_path}")
            except OSError as e:
                logger.warning(f"{prefix} Failed to rescue {rel_path}: {e}")

        # 还原主仓库
        if migrated > 0:
            subprocess.run(
                ["git", "checkout", "--"] + contaminated_files,
                cwd=str(self._root), capture_output=True, text=True,
            )
            logger.warning(
                f"{prefix} Repo contamination detected and rescued: "
                f"{migrated}/{len(contaminated_files)} files migrated to worktree"
            )

        return migrated > 0

    def _save_structured_log(self, task: Task, stdout: str) -> None:
        """解析 stream-json 输出并保存为结构化 JSON 日志。"""
        try:
            import json as _json
            from .monitor import StreamJsonParser
            parser = StreamJsonParser()
            for line in stdout.splitlines():
                parser.parse_line(line)
            structured = parser.to_structured_log(task.id)
            json_log_file = self._logs_dir / f"{task.id}.json"
            json_log_file.write_text(
                _json.dumps(structured, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            logger.warning(f"{self._log_prefix()} Failed to save structured log: {e}")

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
