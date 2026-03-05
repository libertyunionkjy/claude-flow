from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from .config import Config
from .models import Task, TaskStatus


class Planner:
    def __init__(self, project_root: Path, plans_dir: Path, config: Config):
        self._root = project_root
        self._plans_dir = plans_dir
        self._config = config

    def generate(self, task: Task) -> Optional[Path]:
        task.status = TaskStatus.PLANNING
        prompt = f"{self._config.plan_prompt_prefix}\n\n{task.prompt}"
        cmd = ["claude", "-p", prompt, "--print", "--output-format", "text"]
        if self._config.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        result = subprocess.run(
            cmd, cwd=str(self._root),
            capture_output=True, text=True, timeout=self._config.task_timeout,
        )

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
