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
    DONE = "done"
    FAILED = "failed"


def _generate_task_id() -> str:
    short = uuid.uuid4().hex[:6]
    return f"task-{short}"


@dataclass
class Task:
    title: str
    prompt: str
    id: str = field(default_factory=_generate_task_id)
    status: TaskStatus = TaskStatus.PENDING
    branch: Optional[str] = None
    plan_file: Optional[str] = None
    worker_id: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "status": self.status.value,
            "branch": self.branch,
            "plan_file": self.plan_file,
            "worker_id": self.worker_id,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        return cls(
            id=d["id"],
            title=d["title"],
            prompt=d["prompt"],
            status=TaskStatus(d["status"]),
            branch=d.get("branch"),
            plan_file=d.get("plan_file"),
            worker_id=d.get("worker_id"),
            created_at=datetime.fromisoformat(d["created_at"]),
            started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
            completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
            error=d.get("error"),
        )
