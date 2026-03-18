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


class ProjectMode(Enum):
    SINGLE_GIT = "single_git"        # Single git repository (default)
    GIT_SUBMODULE = "git_submodule"  # Git repo with submodules
    MULTI_REPO = "multi_repo"        # Non-git dir with multiple independent git repos
    NON_GIT = "non_git"              # Plain directory, no git


@dataclass
class ManagedRepo:
    """Configuration for a managed git repository in multi-repo workspace."""
    path: str               # Relative path from workspace root, e.g. "project-a"
    alias: str = ""         # Short name for CLI/UI reference
    main_branch: str = "main"
    auto_merge: bool = True
    merge_strategy: str = "--no-ff"
    merge_mode: str = "rebase"
    auto_push: bool = False

    def __post_init__(self):
        if not self.path or not self.path.strip():
            raise ValueError("ManagedRepo path cannot be empty")
        if ".." in self.path.split("/"):
            raise ValueError(f"ManagedRepo path cannot contain '..': {self.path}")
        if self.path.startswith("/"):
            raise ValueError(f"ManagedRepo path must be relative: {self.path}")
        if not self.alias:
            self.alias = self.path.rstrip("/").split("/")[-1]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "alias": self.alias,
            "main_branch": self.main_branch,
            "auto_merge": self.auto_merge,
            "merge_strategy": self.merge_strategy,
            "merge_mode": self.merge_mode,
            "auto_push": self.auto_push,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ManagedRepo:
        return cls(
            path=d["path"],
            alias=d.get("alias", ""),
            main_branch=d.get("main_branch", "main"),
            auto_merge=d.get("auto_merge", True),
            merge_strategy=d.get("merge_strategy", "--no-ff"),
            merge_mode=d.get("merge_mode", "rebase"),
            auto_push=d.get("auto_push", False),
        )


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
    sub_branches: dict[str, str] = field(default_factory=dict)
    use_subagent: Optional[bool] = None  # None = inherit from config
    # Multi-repo workspace fields
    repos: list[str] = field(default_factory=list)                    # Repo paths involved
    repo_base_branches: dict[str, str] = field(default_factory=dict)  # repo_path -> base branch
    repo_merge_targets: dict[str, str] = field(default_factory=dict)  # repo_path -> merge target

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
            "sub_branches": self.sub_branches,
            "use_subagent": self.use_subagent,
            "repos": self.repos,
            "repo_base_branches": self.repo_base_branches,
            "repo_merge_targets": self.repo_merge_targets,
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
            sub_branches=d.get("sub_branches", {}),
            use_subagent=d.get("use_subagent"),
            repos=d.get("repos", []),
            repo_base_branches=d.get("repo_base_branches", {}),
            repo_merge_targets=d.get("repo_merge_targets", {}),
        )
