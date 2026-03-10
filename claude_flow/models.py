from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TaskStatus(Enum):
    PENDING = "pending"
    PLANNING = "planning"
    PLANNED = "planned"
    APPROVED = "approved"
    RUNNING = "running"
    MERGING = "merging"
    NEEDS_INPUT = "needs_input"
    DONE = "done"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TaskType(Enum):
    NORMAL = "normal"
    MINI = "mini"


def _generate_task_id() -> str:
    short = uuid.uuid4().hex[:6]
    return f"task-{short}"


@dataclass
class Task:
    title: str
    prompt: str
    id: str = field(default_factory=_generate_task_id)
    status: TaskStatus = TaskStatus.PENDING
    task_type: TaskType = TaskType.NORMAL
    branch: Optional[str] = None
    plan_file: Optional[str] = None
    worker_id: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    priority: int = 0
    progress: Optional[str] = None
    retry_count: int = 0
    plan_mode: Optional[str] = None  # "auto" | "interactive"
    submodules: list[str] = field(default_factory=list)

    @property
    def is_mini(self) -> bool:
        """Check if this is a mini task (skips planning/approval)."""
        return self.task_type == TaskType.MINI

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "status": self.status.value,
            "task_type": self.task_type.value,
            "branch": self.branch,
            "plan_file": self.plan_file,
            "worker_id": self.worker_id,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "priority": self.priority,
            "progress": self.progress,
            "retry_count": self.retry_count,
            "plan_mode": self.plan_mode,
            "submodules": self.submodules,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        return cls(
            id=d["id"],
            title=d["title"],
            prompt=d["prompt"],
            status=TaskStatus(d["status"]),
            task_type=TaskType(d["task_type"]) if d.get("task_type") else TaskType.NORMAL,
            branch=d.get("branch"),
            plan_file=d.get("plan_file"),
            worker_id=d.get("worker_id"),
            created_at=datetime.fromisoformat(d["created_at"]),
            started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
            completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
            error=d.get("error"),
            priority=d.get("priority", 0),
            progress=d.get("progress"),
            retry_count=d.get("retry_count", 0),
            plan_mode=d.get("plan_mode"),
            submodules=d.get("submodules", []),
        )
