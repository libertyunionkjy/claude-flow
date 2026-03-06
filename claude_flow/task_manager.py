from __future__ import annotations

import fcntl
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .models import Task, TaskStatus

TASKS_FILE = "tasks.json"
LOCK_FILE = "tasks.lock"


class TaskManager:
    def __init__(self, project_root: Path):
        self._root = project_root
        self._cf_dir = project_root / ".claude-flow"
        self._tasks_file = self._cf_dir / TASKS_FILE
        self._lock_file = self._cf_dir / LOCK_FILE

    def _load(self) -> List[Task]:
        if not self._tasks_file.exists():
            return []
        data = json.loads(self._tasks_file.read_text())
        return [Task.from_dict(d) for d in data]

    def _save(self, tasks: List[Task]) -> None:
        self._tasks_file.write_text(
            json.dumps([t.to_dict() for t in tasks], indent=2, ensure_ascii=False)
        )

    def _with_lock(self, fn):
        """Execute fn with exclusive file lock."""
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_file, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def add(self, title: str, prompt: str, priority: int = 0) -> Task:
        def _do():
            tasks = self._load()
            task = Task(title=title, prompt=prompt, priority=priority)
            tasks.append(task)
            self._save(tasks)
            return task
        return self._with_lock(_do)

    def list_tasks(self, status: Optional[TaskStatus] = None) -> List[Task]:
        tasks = self._load()
        if status:
            return [t for t in tasks if t.status == status]
        return tasks

    def get(self, task_id: str) -> Optional[Task]:
        for t in self._load():
            if t.id == task_id:
                return t
        return None

    def remove(self, task_id: str) -> bool:
        def _do():
            tasks = self._load()
            before = len(tasks)
            tasks = [t for t in tasks if t.id != task_id]
            self._save(tasks)
            return len(tasks) < before
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
