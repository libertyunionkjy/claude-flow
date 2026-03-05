from __future__ import annotations

import logging
import subprocess
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

    def _log_prefix(self) -> str:
        return f"[Worker-{self.worker_id}]"

    def execute_task(self, task: Task) -> bool:
        prefix = self._log_prefix()
        logger.info(f"{prefix} Executing: {task.title} ({task.id})")

        # Create worktree
        try:
            wt_path = self._wt.create(task.id, task.branch)
        except subprocess.CalledProcessError as e:
            self._tm.update_status(task.id, TaskStatus.FAILED, f"Worktree creation failed: {e.stderr}")
            return False

        # Run Claude Code in worktree
        prompt = f"{self._cfg.task_prompt_prefix}\n\n{task.prompt}"
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if self._cfg.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(self._cfg.claude_args)

        log_file = self._logs_dir / f"{task.id}.log"
        try:
            result = subprocess.run(
                cmd, cwd=str(wt_path),
                capture_output=True, text=True,
                timeout=self._cfg.task_timeout,
            )
            log_file.write_text(result.stdout + "\n" + result.stderr)
        except subprocess.TimeoutExpired:
            self._tm.update_status(task.id, TaskStatus.FAILED, "Timeout")
            self._wt.remove(task.id, task.branch)
            return False

        if result.returncode != 0:
            self._tm.update_status(task.id, TaskStatus.FAILED, f"Exit code {result.returncode}")
            self._wt.remove(task.id, task.branch)
            return False

        # Merge
        if self._cfg.auto_merge:
            self._tm.update_status(task.id, TaskStatus.MERGING)
            success = self._wt.merge(task.branch, self._cfg.main_branch, self._cfg.merge_strategy)
            if not success:
                self._tm.update_status(task.id, TaskStatus.FAILED, "CONFLICT")
                return False

        # Cleanup
        self._wt.remove(task.id, task.branch)
        self._tm.update_status(task.id, TaskStatus.DONE)
        logger.info(f"{prefix} Done: {task.title}")
        return True

    def run_loop(self) -> int:
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
