from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from .chat import ChatSession
from .config import Config
from .models import Task, TaskStatus
from .utils import can_skip_permissions

if TYPE_CHECKING:
    from .task_manager import TaskManager


class Planner:
    def __init__(
        self,
        project_root: Path,
        plans_dir: Path,
        config: Config,
        task_manager: Optional[TaskManager] = None,
    ):
        self._root = project_root
        self._plans_dir = plans_dir
        self._config = config
        self._task_manager = task_manager

    # ------------------------------------------------------------------
    # 子进程执行（支持 Ctrl+C 中断）
    # ------------------------------------------------------------------

    def _run_claude(self, cmd: list[str]) -> subprocess.CompletedProcess:
        """Run claude CLI with proper Ctrl+C (SIGINT) handling.

        Uses Popen so that KeyboardInterrupt immediately terminates the
        child process instead of blocking until pipe EOF.

        Raises:
            KeyboardInterrupt: re-raised after child is terminated.
            subprocess.TimeoutExpired: if task_timeout is exceeded.
            OSError: if the command cannot be started.
        """
        proc = subprocess.Popen(
            cmd, cwd=str(self._root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=self._config.task_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
        except KeyboardInterrupt:
            # Terminate child immediately on Ctrl+C
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    # ------------------------------------------------------------------
    # 原有方法（保持签名不变）
    # ------------------------------------------------------------------

    # Tools that must never be used during the planning phase
    _PLANNING_DISALLOWED_TOOLS = ["Write", "Edit", "Bash", "NotebookEdit"]

    # Default read-only tools allowed during the planning phase.
    # Used as fallback when plan_allowed_tools is not explicitly configured.
    _PLANNING_DEFAULT_ALLOWED_TOOLS = ["Read", "Glob", "Grep"]

    def _build_plan_cmd(self, prompt: str) -> list[str]:
        """Build claude CLI command for plan-phase invocations.

        Applies plan_allowed_tools restriction when configured.
        Additionally, always appends --disallowedTools to explicitly
        block write/execute tools, preventing AI from writing files
        even when --dangerously-skip-permissions is active.

        When --dangerously-skip-permissions cannot be used (e.g. running
        as root), falls back to --permission-mode plan combined with
        --allowedTools to ensure read-only tools are auto-authorized
        in non-interactive mode.
        """
        cmd = ["claude", "-p", prompt, "--print", "--output-format", "text"]
        skip = can_skip_permissions(self._config.skip_permissions)
        if skip:
            cmd.append("--dangerously-skip-permissions")

        # Determine allowed tools: explicit config > class default
        allowed = (
            self._config.plan_allowed_tools
            if self._config.plan_allowed_tools
            else self._PLANNING_DEFAULT_ALLOWED_TOOLS
        )
        cmd.extend(["--allowedTools"] + allowed)

        if not skip:
            # Cannot use --dangerously-skip-permissions (e.g. root).
            # Use --permission-mode plan so read-only tools are
            # auto-authorized without interactive confirmation.
            cmd.extend(["--permission-mode", "plan"])

        cmd.extend(["--disallowedTools"] + self._PLANNING_DISALLOWED_TOOLS)
        return cmd

    def generate(self, task: Task) -> Optional[Path]:
        """调用 Claude Code 生成计划并保存为 .md 文件。"""
        task.status = TaskStatus.PLANNING
        prompt = f"{self._config.plan_prompt_prefix}\n\n{task.prompt}"
        cmd = self._build_plan_cmd(prompt)

        try:
            result = self._run_claude(cmd)
        except subprocess.TimeoutExpired:
            task.status = TaskStatus.FAILED
            task.error = f"Plan generation timed out after {self._config.task_timeout}s"
            return None
        except OSError as e:
            task.status = TaskStatus.FAILED
            task.error = f"Plan generation failed: {e}"
            return None

        if result.returncode != 0:
            task.status = TaskStatus.FAILED
            task.error = f"Plan generation failed: {result.stderr}"
            return None

        plan_file = self._plans_dir / f"{task.id}.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(result.stdout)
        task.status = TaskStatus.PLANNED
        task.plan_file = str(plan_file)
        return plan_file

    def read_plan(self, plan_path: Path) -> str:
        return plan_path.read_text()

    def approve(self, task: Task) -> None:
        task.status = TaskStatus.APPROVED

    # ------------------------------------------------------------------
    # Chat-based plan generation
    # ------------------------------------------------------------------

    def generate_from_chat(
        self, task: Task, chat_session: ChatSession
    ) -> Optional[Path]:
        """Generate a structured plan document from a chat session.

        Builds a prompt from the conversation history and calls Claude
        to produce a final implementation plan in markdown format.
        """
        task.status = TaskStatus.PLANNING

        # Determine version number
        existing_versions = self.list_versions(task.id)
        next_version = len(existing_versions) + 1

        # Build prompt from chat history
        prompt_parts = [
            self._config.plan_prompt_prefix,
            "",
            f"## Task: {task.title}",
            f"{task.prompt}",
            "",
            "## Discussion Summary",
            "",
        ]
        for msg in chat_session.messages:
            prefix = "User" if msg.role == "user" else "Assistant"
            prompt_parts.append(f"**{prefix}**: {msg.content}")
            prompt_parts.append("")

        prompt_parts.append(
            "Based on the above discussion, generate a final structured "
            "implementation plan in markdown format."
        )
        prompt = "\n".join(prompt_parts)

        cmd = self._build_plan_cmd(prompt)

        try:
            result = self._run_claude(cmd)
        except subprocess.TimeoutExpired:
            task.status = TaskStatus.FAILED
            task.error = f"Plan generation timed out after {self._config.task_timeout}s"
            return None
        except OSError as e:
            task.status = TaskStatus.FAILED
            task.error = f"Plan generation failed: {e}"
            return None

        if result.returncode != 0:
            task.status = TaskStatus.FAILED
            task.error = f"Plan generation failed: {result.stderr}"
            return None

        plan_content = result.stdout

        # Format and save
        formatted = self._format_plan(task, plan_content, version=next_version)

        self._plans_dir.mkdir(parents=True, exist_ok=True)
        version_file = self._plans_dir / f"{task.id}_v{next_version}.md"
        version_file.write_text(formatted)

        current_plan_file = self._plans_dir / f"{task.id}.md"
        current_plan_file.write_text(formatted)

        task.status = TaskStatus.PLANNED
        task.plan_file = str(current_plan_file)
        return version_file

    # ------------------------------------------------------------------
    # 新增：Plan 拆分为子任务
    # ------------------------------------------------------------------

    def split_plan(self, task: Task, sub_tasks: List[dict]) -> List[Task]:
        """将一个计划拆分为多个子任务。

        sub_tasks: [{"title": "...", "prompt": "..."}, ...]
        返回创建的子任务列表。

        注意：此方法需要 TaskManager，通过构造函数接收。

        Raises:
            RuntimeError: 如果未提供 TaskManager。
            ValueError: 如果 sub_tasks 为空或条目格式不正确。
        """
        if self._task_manager is None:
            raise RuntimeError(
                "split_plan 需要 TaskManager，请在构造 Planner 时传入 task_manager 参数。"
            )
        if not sub_tasks:
            raise ValueError("sub_tasks 不能为空。")

        created: List[Task] = []
        for entry in sub_tasks:
            title = entry.get("title")
            prompt = entry.get("prompt")
            if not title or not prompt:
                raise ValueError(
                    f"每个子任务必须包含非空的 title 和 prompt，收到: {entry}"
                )
            new_task = self._task_manager.add(title=title, prompt=prompt)
            created.append(new_task)

        return created

    # ------------------------------------------------------------------
    # 新增：结构化计划格式
    # ------------------------------------------------------------------

    def _format_plan(
        self, task: Task, plan_content: str, version: int = 1
    ) -> str:
        """将计划内容格式化为结构化格式（YAML front matter + Markdown body）。"""
        now = datetime.now().replace(microsecond=0).isoformat()
        front_matter = (
            f"---\n"
            f"task_id: {task.id}\n"
            f"title: {task.title}\n"
            f"version: {version}\n"
            f"created_at: {now}\n"
            f"status: planned\n"
            f"---\n"
        )
        return f"{front_matter}\n# 实施计划\n\n{plan_content}\n"

    # ------------------------------------------------------------------
    # 新增：计划版本管理
    # ------------------------------------------------------------------

    def list_versions(self, task_id: str) -> List[Path]:
        """列出某个任务的所有计划版本文件，按版本号升序排列。"""
        if not self._plans_dir.exists():
            return []

        # 匹配 {task_id}_v{数字}.md 格式的文件
        pattern = re.compile(rf"^{re.escape(task_id)}_v(\d+)\.md$")
        versions: list[tuple[int, Path]] = []

        for f in self._plans_dir.iterdir():
            match = pattern.match(f.name)
            if match:
                ver_num = int(match.group(1))
                versions.append((ver_num, f))

        # 按版本号升序排列
        versions.sort(key=lambda x: x[0])
        return [path for _, path in versions]
