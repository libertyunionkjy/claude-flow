from __future__ import annotations

import re
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from .config import Config
from .models import Task, TaskStatus

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

    def generate(self, task: Task) -> Optional[Path]:
        """调用 Claude Code 生成计划并保存为 .md 文件。"""
        task.status = TaskStatus.PLANNING
        prompt = f"{self._config.plan_prompt_prefix}\n\n{task.prompt}"
        cmd = ["claude", "-p", prompt, "--print", "--output-format", "text"]
        if self._config.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

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

    def reject(self, task: Task, reason: str) -> None:
        task.prompt += f"\n\n注意：上次的方案被拒绝，原因：{reason}，请重新规划。"
        task.status = TaskStatus.PENDING

    # ------------------------------------------------------------------
    # 新增：多轮对话支持
    # ------------------------------------------------------------------

    def generate_interactive(
        self, task: Task, feedback: Optional[str] = None
    ) -> Optional[Path]:
        """支持多轮对话的计划生成。

        如果 feedback 不为 None，将 feedback 作为用户反馈附加到 prompt 中，
        让 Claude 基于之前的计划和反馈重新生成。
        """
        task.status = TaskStatus.PLANNING

        # 确定当前版本号
        existing_versions = self.list_versions(task.id)
        next_version = len(existing_versions) + 1

        # 构建 prompt
        prompt_parts = [self._config.plan_prompt_prefix, "", task.prompt]

        # 如果存在之前的计划文件，读取其内容作为上下文
        current_plan_file = self._plans_dir / f"{task.id}.md"
        if current_plan_file.exists():
            previous_plan = current_plan_file.read_text()
            prompt_parts.append("")
            prompt_parts.append("--- 之前的计划 ---")
            prompt_parts.append(previous_plan)

        # 附加用户反馈
        if feedback is not None:
            prompt_parts.append("")
            prompt_parts.append(f"--- 用户反馈 ---\n{feedback}")
            prompt_parts.append("")
            prompt_parts.append("请根据以上反馈对计划进行改进和重新生成。")

        prompt = "\n".join(prompt_parts)

        cmd = ["claude", "-p", prompt, "--print", "--output-format", "text"]
        if self._config.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

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

        # 格式化为结构化计划
        formatted = self._format_plan(task, plan_content, version=next_version)

        # 保存版本文件 {task_id}_v{version}.md
        self._plans_dir.mkdir(parents=True, exist_ok=True)
        version_file = self._plans_dir / f"{task.id}_v{next_version}.md"
        version_file.write_text(formatted)

        # 同时更新 {task_id}.md 为最新版本
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
