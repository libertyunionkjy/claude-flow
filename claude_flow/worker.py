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
from .worktree import MultiRepoWorktreeManager, WorktreeManager

logger = logging.getLogger(__name__)

SUBAGENT_PROMPT = (
    "当你面对此任务时，请考虑将其拆分为多个独立子任务并行处理。\n"
    "使用 Task tool 启动 subagent 来并行执行这些子任务。\n"
    "每个 subagent 应该有明确的职责边界，独立完成后汇总结果。\n"
    "优先使用 general-purpose 类型的 subagent。\n"
    "如果子任务之间有依赖关系，按依赖顺序串行执行。\n"
    "如果任务足够简单不需要拆分，直接执行即可。"
)


class Worker:
    def __init__(
        self,
        worker_id: int,
        project_root: Path,
        task_manager: TaskManager,
        worktree_manager: WorktreeManager,
        config: Config,
        is_git: bool = True,
        project_mode: str = "single_git",
        multi_repo_wt: Optional[MultiRepoWorktreeManager] = None,
    ):
        self.worker_id = worker_id
        self._root = project_root
        self._tm = task_manager
        self._wt = worktree_manager
        self._cfg = config
        self._is_git = is_git
        self._project_mode = project_mode
        self._multi_wt = multi_repo_wt
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

    def _append_permission_flags(self, cmd: list[str]) -> None:
        """Append permission flags to a claude CLI command.

        Uses --dangerously-skip-permissions when available (non-root).
        Falls back to --permission-mode bypassPermissions for root
        environments where the flag is rejected, ensuring the worker
        can still execute read/write tools in non-interactive mode.
        """
        if can_skip_permissions(self._cfg.skip_permissions):
            cmd.append("--dangerously-skip-permissions")
        elif self._cfg.skip_permissions:
            # skip_permissions requested but blocked (e.g. root).
            cmd.extend(["--permission-mode", "bypassPermissions"])

    def _build_prompt(self, task: Task) -> str:
        """Build the full prompt for a task, optionally injecting subagent instructions."""
        parts = [self._cfg.task_prompt_prefix, task.prompt]

        use_subagent = (
            task.use_subagent
            if task.use_subagent is not None
            else self._cfg.use_subagent
        )
        if use_subagent:
            parts.append(SUBAGENT_PROMPT)

        return "\n\n".join(parts)

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
        if self._project_mode == "multi_repo" and len(task.repos) > 0:
            return self._execute_task_multi_repo(task)
        elif self._is_git:
            return self._execute_task_git(task)
        else:
            return self._execute_task_simple(task)

    def _execute_task_simple(self, task: Task) -> bool:
        """Non-git mode: run Claude Code directly in project root without worktree isolation."""
        prefix = self._log_prefix()
        wt_path = self._root

        prompt = self._build_prompt(task)
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        self._append_permission_flags(cmd)
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
            return False

        if returncode != 0:
            error_msg = f"Exit code {returncode}"
            self._tm.update_status(task.id, TaskStatus.FAILED, error_msg)
            return False

        # 合并前测试验证（non-git mode 也支持）
        if self._cfg.pre_merge_commands:
            test_passed = self._run_pre_merge_tests(task, wt_path)
            if not test_passed:
                self._tm.update_status(task.id, TaskStatus.FAILED, "Pre-merge tests failed")
                return False

        self._tm.update_status(task.id, TaskStatus.DONE)
        logger.info(f"{prefix} Done (non-git): {task.title}")
        return True

    def _execute_task_git(self, task: Task) -> bool:
        """Git mode: full worktree isolation, commit, merge flow."""
        prefix = self._log_prefix()

        # Create worktree (pass config for symlink setup, sub_branches for submodule branch init)
        try:
            wt_path = self._wt.create(task.id, task.branch, config=self._cfg,
                                      submodules=task.submodules or None,
                                      sub_branches=task.sub_branches or None)
        except subprocess.CalledProcessError as e:
            self._tm.update_status(task.id, TaskStatus.FAILED, f"Worktree creation failed: {e.stderr}")
            return False

        # 运行 Claude Code（注入 worktree 路径约束）
        worktree_constraint = (
            f"重要：你的项目工作目录是 {wt_path}。"
            f"所有文件操作（读取、编辑、创建）必须在此目录下进行，"
            f"禁止操作 {self._root} 路径下的文件。"
        )
        base_prompt = self._build_prompt(task)
        prompt = f"{base_prompt}\n\n{worktree_constraint}"
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        self._append_permission_flags(cmd)
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
            self._wt.remove(task.id, task.branch)
            return False

        if returncode != 0:
            error_msg = f"Exit code {returncode}"
            self._tm.update_status(task.id, TaskStatus.FAILED, error_msg)
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

        # Merge submodule branches back to their target branches and update pointers
        if task.submodules and task.sub_branches:
            sub_merge_ok = self._wt.merge_submodules(wt_path, task)
            if not sub_merge_ok:
                self._tm.update_status(task.id, TaskStatus.FAILED, "Submodule merge failed")
                self._wt.remove(task.id, task.branch)
                return False
            # Re-commit to update submodule pointers in the main repo
            self._auto_commit(task, wt_path)
            # Optionally push submodule changes
            if self._cfg.auto_push_submodules:
                self._wt.push_submodules(wt_path, task)

        # 检查分支上是否有新 commit（相对于 main）
        has_new_commits = has_changes or self._has_new_commits(task.branch, wt_path)

        if not has_new_commits:
            # Claude 成功执行但没有产生代码变更，跳过合并，直接标记完成
            logger.info(f"{prefix} No code changes detected, skipping merge for {task.id}")
        else:
            # 有代码变更，走正常合并流程

            # 合并前测试验证
            if self._cfg.pre_merge_commands:
                test_passed = self._run_pre_merge_tests(task, wt_path)
                if not test_passed:
                    self._tm.update_status(task.id, TaskStatus.FAILED, "Pre-merge tests failed")
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

        # 清理
        self._wt.remove(task.id, task.branch)
        self._tm.update_status(task.id, TaskStatus.DONE)
        logger.info(f"{prefix} Done: {task.title}")
        return True

    def _execute_task_multi_repo(self, task: Task) -> bool:
        """Multi-repo mode: create composite worktree, run Claude, commit/merge per repo."""
        prefix = self._log_prefix()

        # 1. 确定每个仓库的基础分支
        repo_branches: dict[str, str] = {}
        for repo_path in task.repos:
            base = task.repo_base_branches.get(repo_path)
            if not base:
                repo_cfg = self._cfg.get_repo_by_path(repo_path)
                base = repo_cfg.main_branch if repo_cfg else "main"
            repo_branches[repo_path] = base

        # 2. 创建组合工作目录
        try:
            composite_path = self._multi_wt.create_composite(task.id, repo_branches)
        except Exception as e:
            self._tm.update_status(task.id, TaskStatus.FAILED,
                                   f"Composite creation failed: {e}")
            return False

        logger.info(f"{prefix} Created composite worktree at {composite_path}")

        try:
            # 3. 构建 prompt（注入多仓库目录结构说明）
            prompt = self._build_multi_repo_prompt(task, composite_path)

            # 4. 构建 claude 命令
            cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
            self._append_permission_flags(cmd)
            cmd.extend(self._cfg.claude_args)

            # 5. 运行 Claude Code（cwd = composite_path）
            log_file = self._logs_dir / f"{task.id}.log"
            json_log_file = self._logs_dir / f"{task.id}.json"
            env = os.environ.copy()
            env["PORT"] = str(self.port)
            env["WORKER_ID"] = str(self.worker_id)

            try:
                returncode = self._run_streaming(
                    cmd, cwd=str(composite_path), env=env,
                    task=task, log_file=log_file, json_log_file=json_log_file,
                )
            except subprocess.TimeoutExpired:
                self._tm.update_status(task.id, TaskStatus.FAILED, "Timeout")
                return False

            if returncode != 0:
                error_msg = f"Exit code {returncode}"
                self._tm.update_status(task.id, TaskStatus.FAILED, error_msg)
                return False

            # 6. Per-repo 自动提交
            self._tm.update_status(task.id, TaskStatus.MERGING)
            commit_results = self._multi_wt.commit_repos(task.id, composite_path, task.repos)

            has_any_changes = any(commit_results.values())
            if not has_any_changes:
                logger.info(f"{prefix} No changes in any repo, marking done")
                self._tm.update_status(task.id, TaskStatus.DONE)
                return True

            # 7. Pre-merge tests（在组合目录中运行）
            if self._cfg.pre_merge_commands:
                test_ok = self._run_pre_merge_tests(task, str(composite_path))
                if not test_ok:
                    self._tm.update_status(task.id, TaskStatus.FAILED,
                                           "Pre-merge tests failed")
                    return False

            # 8. Per-repo 合并
            if self._cfg.auto_merge:
                merge_targets: dict[str, str] = {}
                for repo_path in task.repos:
                    if not commit_results.get(repo_path):
                        continue  # 该仓库无变更，跳过合并
                    target = task.repo_merge_targets.get(repo_path)
                    if not target:
                        target = task.repo_base_branches.get(repo_path)
                    if not target:
                        repo_cfg = self._cfg.get_repo_by_path(repo_path)
                        target = repo_cfg.main_branch if repo_cfg else "main"
                    merge_targets[repo_path] = target

                if merge_targets:
                    merge_results = self._multi_wt.merge_repos(task.id, merge_targets)

                    failed_repos = [r for r, ok in merge_results.items() if not ok]
                    if failed_repos:
                        self._tm.update_status(
                            task.id, TaskStatus.FAILED,
                            f"Merge failed for repos: {', '.join(failed_repos)}")
                        return False

            # 9. 清理（成功路径）
            self._multi_wt.remove_composite(task.id, task.repos)
            self._tm.update_status(task.id, TaskStatus.DONE)
            logger.info(f"{prefix} Done (multi-repo): {task.title}")
            return True

        except Exception as e:
            self._tm.update_status(task.id, TaskStatus.FAILED,
                                   f"Multi-repo execution error: {e}")
            return False
        finally:
            # Ensure cleanup (if success path already called remove_composite,
            # this will quietly skip since the directory no longer exists)
            if composite_path.exists():
                try:
                    self._multi_wt.remove_composite(task.id, task.repos)
                except Exception:
                    pass  # best effort

    def _build_multi_repo_prompt(self, task: Task, composite_path: Path) -> str:
        """Build prompt with multi-repo workspace context injected."""
        prompt = self._build_prompt(task)

        repo_info = []
        for repo_path in task.repos:
            base = task.repo_base_branches.get(repo_path, "main")
            repo_info.append(f"  - {repo_path}/ (基于分支: {base})")

        constraint = (
            f"\n\n[多仓库工作区]\n"
            f"你正在一个包含多个独立项目的工作区中工作。\n"
            f"当前工作目录: {composite_path}\n"
            f"包含以下项目:\n"
            + "\n".join(repo_info)
            + f"\n请只在以上目录中修改文件。每个子目录是一个独立的 git 仓库。\n"
        )

        return prompt + constraint

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
                    self._append_permission_flags(fix_cmd)
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

        对于带 submodule 的任务，执行两步提交：
        1. 先在每个 submodule 中独立提交
        2. 再在主项目中提交（捕获 submodule 指针更新）

        返回 True 表示有变更并已提交，False 表示无变更。
        """
        prefix = self._log_prefix()

        # 步骤 1: 对每个 submodule 单独提交
        for sub_path in task.submodules:
            sub_dir = wt_path / sub_path
            if not sub_dir.exists():
                continue
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(sub_dir), capture_output=True, text=True,
            )
            if status_result.stdout.strip():
                logger.info(f"{prefix} Auto-committing submodule {sub_path} for {task.id}")
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=str(sub_dir), capture_output=True, text=True,
                )
                # Submodule 的 git config 可能缺少 user.email/user.name，
                # 从主 worktree 继承这些配置以确保 commit 成功。
                commit_cmd = ["git"]
                user_email = subprocess.run(
                    ["git", "config", "user.email"],
                    cwd=str(wt_path), capture_output=True, text=True,
                ).stdout.strip()
                user_name = subprocess.run(
                    ["git", "config", "user.name"],
                    cwd=str(wt_path), capture_output=True, text=True,
                ).stdout.strip()
                if user_email:
                    commit_cmd.extend(["-c", f"user.email={user_email}"])
                if user_name:
                    commit_cmd.extend(["-c", f"user.name={user_name}"])
                commit_cmd.extend([
                    "commit", "-m", f"feat({task.id}): {task.title}",
                    "--no-verify",
                ])
                subprocess.run(
                    commit_cmd,
                    cwd=str(sub_dir), capture_output=True, text=True,
                )

        # 步骤 2: 主项目提交（包含 submodule 指针更新 + 其他改动）
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        if not status_result.stdout.strip():
            return False

        logger.info(f"{prefix} Auto-committing changes for {task.id}")
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


