from __future__ import annotations

import fcntl
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .models import Task, TaskStatus, TaskType

TASKS_FILE = "tasks.json"
TASKS_BACKUP = "tasks.json.bak"
LOCK_FILE = "tasks.lock"

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self, project_root: Path):
        self._root = project_root
        self._cf_dir = project_root / ".claude-flow"
        self._tasks_file = self._cf_dir / TASKS_FILE
        self._backup_file = self._cf_dir / TASKS_BACKUP
        self._lock_file = self._cf_dir / LOCK_FILE

    def _load(self) -> List[Task]:
        if not self._tasks_file.exists():
            return []
        content = self._tasks_file.read_text().strip()
        if not content:
            # Main file is empty/corrupt -- try backup
            return self._load_from_backup()
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("tasks.json is corrupted, recovering from backup")
            return self._load_from_backup()
        return [Task.from_dict(d) for d in data]

    def _load_from_backup(self) -> List[Task]:
        if not self._backup_file.exists():
            return []
        content = self._backup_file.read_text().strip()
        if not content:
            return []
        try:
            data = json.loads(content)
            logger.info("Recovered %d tasks from backup", len(data))
            # Restore main file from backup
            self._atomic_write(self._tasks_file, content)
            return [Task.from_dict(d) for d in data]
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Backup file is also corrupted: %s", e)
            return []

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        """Write content to target file atomically via temp file + os.replace."""
        tmp = target.with_suffix(".tmp")
        tmp.write_text(content)
        os.replace(tmp, target)

    def _save(self, tasks: List[Task]) -> None:
        content = json.dumps(
            [t.to_dict() for t in tasks], indent=2, ensure_ascii=False
        )
        # Backup: hard-link current file before overwriting (cheap, no IO copy)
        if self._tasks_file.exists():
            try:
                # Remove stale backup, then hard-link current -> backup
                self._backup_file.unlink(missing_ok=True)
                os.link(self._tasks_file, self._backup_file)
            except OSError:
                pass  # Best-effort backup
        # Atomic write to main file
        self._atomic_write(self._tasks_file, content)

    def _with_lock(self, fn):
        """Execute fn with exclusive file lock."""
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_file, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _with_shared_lock(self, fn):
        """Execute fn with shared file lock (allows concurrent reads)."""
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_file, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            try:
                return fn()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def add(self, title: str, prompt: str, priority: int = 0, submodules: list[str] | None = None,
            use_subagent: bool | None = None) -> Task:
        def _do():
            tasks = self._load()
            task = Task(title=title, prompt=prompt, priority=priority, submodules=submodules or [],
                        use_subagent=use_subagent)
            tasks.append(task)
            self._save(tasks)
            return task
        return self._with_lock(_do)

    def add_mini(self, title: str, prompt: str, priority: int = 0, submodules: list[str] | None = None) -> Task:
        """Add a mini task that skips planning/approval and is immediately executable."""
        def _do():
            tasks = self._load()
            task = Task(
                title=title,
                prompt=prompt,
                priority=priority,
                task_type=TaskType.MINI,
                status=TaskStatus.APPROVED,
                submodules=submodules or [],
            )
            tasks.append(task)
            self._save(tasks)
            return task
        return self._with_lock(_do)

    def list_tasks(
        self, status: Optional[TaskStatus] = None, task_type: Optional[str] = None
    ) -> List[Task]:
        def _do():
            tasks = self._load()
            if status:
                tasks = [t for t in tasks if t.status == status]
            if task_type:
                tasks = [t for t in tasks if t.task_type.value == task_type]
            return tasks
        return self._with_shared_lock(_do)

    def get(self, task_id: str) -> Optional[Task]:
        def _do():
            for t in self._load():
                if t.id == task_id:
                    return t
            return None
        return self._with_shared_lock(_do)

    def remove(self, task_id: str) -> Optional[Task]:
        """Remove a task and return it (or None if not found)."""
        def _do():
            tasks = self._load()
            removed = None
            for t in tasks:
                if t.id == task_id:
                    removed = t
                    break
            if removed is None:
                return None
            tasks = [t for t in tasks if t.id != task_id]
            self._save(tasks)
            return removed
        return self._with_lock(_do)

    def update_status(
        self, task_id: str, status: TaskStatus, error: Optional[str] = None
    ) -> Optional[Task]:
        def _do():
            tasks = self._load()
            for t in tasks:
                if t.id == task_id:
                    t.status = status
                    if error:
                        t.error = error
                    if status == TaskStatus.RUNNING:
                        t.started_at = datetime.now()
                    elif status in (TaskStatus.DONE, TaskStatus.FAILED):
                        t.completed_at = datetime.now()
                    self._save(tasks)
                    return t
            return None
        return self._with_lock(_do)

    def claim_next(self, worker_id: int) -> Optional[Task]:
        def _do():
            tasks = self._load()
            # 筛选所有已批准的任务
            approved = [t for t in tasks if t.status == TaskStatus.APPROVED]
            if not approved:
                return None
            # 按 priority 降序排序（数字越大优先级越高）
            approved.sort(key=lambda t: t.priority, reverse=True)
            target = approved[0]
            # 在原 tasks 列表中找到对应任务并更新状态
            for t in tasks:
                if t.id == target.id:
                    t.status = TaskStatus.RUNNING
                    t.worker_id = worker_id
                    t.started_at = datetime.now()
                    t.branch = f"cf/{t.id}"
                    self._save(tasks)
                    return t
            return None
        return self._with_lock(_do)

    def update_priority(self, task_id: str, priority: int) -> Optional[Task]:
        """更新任务优先级（线程安全）。"""
        def _do():
            tasks = self._load()
            for t in tasks:
                if t.id == task_id:
                    t.priority = priority
                    self._save(tasks)
                    return t
            return None
        return self._with_lock(_do)

    def update_progress(self, task_id: str, progress: str) -> Optional[Task]:
        """更新任务进度描述（线程安全）。"""
        def _do():
            tasks = self._load()
            for t in tasks:
                if t.id == task_id:
                    t.progress = progress
                    self._save(tasks)
                    return t
            return None
        return self._with_lock(_do)

    def respond(self, task_id: str, additional_input: str) -> Optional[Task]:
        """为 needs_input 状态的任务补充信息并重置为 approved。

        将补充信息追加到原始 prompt 末尾，清除 error，重置为 approved 状态。
        """
        def _do():
            tasks = self._load()
            for t in tasks:
                if t.id == task_id:
                    if t.status != TaskStatus.NEEDS_INPUT:
                        return None
                    t.prompt += f"\n\n[补充信息] {additional_input}"
                    t.status = TaskStatus.APPROVED
                    t.error = None
                    t.worker_id = None
                    t.started_at = None
                    t.completed_at = None
                    t.branch = None
                    t.progress = None
                    self._save(tasks)
                    return t
            return None
        return self._with_lock(_do)

    def add_from_file(self, filepath: Path) -> List[Task]:
        added = []
        for line in filepath.read_text().strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                title, prompt = line.split("|", 1)
                title, prompt = title.strip(), prompt.strip()
                if not title or not prompt:
                    continue
                added.append(self.add(title, prompt))
            else:
                added.append(self.add(line, line))
        return added
