# Claude Flow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python CLI tool (`cf`) that manages multiple Claude Code instances with task queues, git worktree parallelization, and plan mode review workflow.

**Architecture:** Modular Python package with Click CLI. TaskManager handles JSON-based task CRUD with file locking. Workers run as subprocesses, each in an isolated git worktree. Planner wraps Claude Code's plan mode for batch plan generation and interactive review.

**Tech Stack:** Python 3.10+, Click, fcntl, subprocess, dataclasses, JSON

---

### Task 1: Project Scaffolding + Models

**Files:**
- Create: `pyproject.toml`
- Create: `claude_flow/__init__.py`
- Create: `claude_flow/models.py`
- Create: `tests/__init__.py`
- Create: `tests/test_models.py`

**Step 1: Create project directory and pyproject.toml**

```bash
mkdir -p claude_flow tests
```

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=64", "setuptools-scm"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "claude-flow"
version = "0.1.0"
description = "Multi-instance Claude Code workflow manager"
requires-python = ">=3.10"
dependencies = ["click>=8.0"]

[project.scripts]
cf = "claude_flow.cli:main"

[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov"]
```

**Step 2: Write failing tests for models**

```python
# tests/test_models.py
import json
from datetime import datetime
from claude_flow.models import Task, TaskStatus


class TestTaskStatus:
    def test_status_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.PLANNING.value == "planning"
        assert TaskStatus.PLANNED.value == "planned"
        assert TaskStatus.APPROVED.value == "approved"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.MERGING.value == "merging"
        assert TaskStatus.DONE.value == "done"
        assert TaskStatus.FAILED.value == "failed"


class TestTask:
    def test_create_task(self):
        task = Task(title="Test task", prompt="Do something")
        assert task.title == "Test task"
        assert task.prompt == "Do something"
        assert task.status == TaskStatus.PENDING
        assert task.id.startswith("task-")
        assert task.branch is None
        assert task.worker_id is None
        assert task.error is None
        assert isinstance(task.created_at, datetime)

    def test_task_to_dict(self):
        task = Task(title="Test", prompt="Prompt")
        d = task.to_dict()
        assert d["title"] == "Test"
        assert d["prompt"] == "Prompt"
        assert d["status"] == "pending"
        assert "created_at" in d

    def test_task_from_dict(self):
        now = datetime.now()
        d = {
            "id": "task-001",
            "title": "Test",
            "prompt": "Prompt",
            "status": "pending",
            "branch": None,
            "plan_file": None,
            "worker_id": None,
            "created_at": now.isoformat(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        task = Task.from_dict(d)
        assert task.id == "task-001"
        assert task.status == TaskStatus.PENDING

    def test_task_roundtrip(self):
        task = Task(title="Roundtrip", prompt="Test prompt")
        d = task.to_dict()
        restored = Task.from_dict(d)
        assert restored.id == task.id
        assert restored.title == task.title
        assert restored.status == task.status

    def test_task_auto_id_increments(self):
        t1 = Task(title="A", prompt="a")
        t2 = Task(title="B", prompt="b")
        assert t1.id != t2.id

    def test_task_branch_name(self):
        task = Task(title="Test", prompt="P")
        task.branch = f"cf/{task.id}"
        assert task.branch.startswith("cf/task-")
```

**Step 3: Run tests to verify they fail**

```bash
pip install -e ".[dev]" && pytest tests/test_models.py -v
```

Expected: FAIL (module not found)

**Step 4: Implement models**

```python
# claude_flow/__init__.py
"""Claude Flow - Multi-instance Claude Code workflow manager."""
__version__ = "0.1.0"
```

```python
# claude_flow/models.py
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
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/test_models.py -v
```

Expected: All PASS

**Step 6: Commit**

```bash
git add -A && git commit -m "feat: project scaffolding and Task/TaskStatus models"
```

---

### Task 2: Config Module

**Files:**
- Create: `claude_flow/config.py`
- Create: `tests/test_config.py`

**Step 1: Write failing tests**

```python
# tests/test_config.py
import json
import os
from pathlib import Path
from claude_flow.config import Config, DEFAULT_CONFIG


class TestConfig:
    def test_default_config(self):
        cfg = Config()
        assert cfg.max_workers == 2
        assert cfg.main_branch == "main"
        assert cfg.auto_merge is True
        assert cfg.skip_permissions is True
        assert cfg.task_timeout == 600

    def test_load_from_file(self, tmp_path):
        config_dir = tmp_path / ".claude-flow"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"max_workers": 5, "main_branch": "develop"}))
        cfg = Config.load(tmp_path)
        assert cfg.max_workers == 5
        assert cfg.main_branch == "develop"
        # defaults still apply for unset keys
        assert cfg.auto_merge is True

    def test_load_missing_file_returns_defaults(self, tmp_path):
        cfg = Config.load(tmp_path)
        assert cfg.max_workers == 2

    def test_save_config(self, tmp_path):
        config_dir = tmp_path / ".claude-flow"
        config_dir.mkdir()
        cfg = Config(max_workers=3)
        cfg.save(tmp_path)
        loaded = json.loads((config_dir / "config.json").read_text())
        assert loaded["max_workers"] == 3

    def test_claude_flow_dir(self, tmp_path):
        cfg = Config()
        d = cfg.claude_flow_dir(tmp_path)
        assert d == tmp_path / ".claude-flow"
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config.py -v
```

**Step 3: Implement config**

```python
# claude_flow/config.py
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

CLAUDE_FLOW_DIR = ".claude-flow"
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "max_workers": 2,
    "main_branch": "main",
    "claude_args": [],
    "auto_merge": True,
    "merge_strategy": "--no-ff",
    "worktree_dir": ".claude-flow/worktrees",
    "skip_permissions": True,
    "plan_prompt_prefix": "请分析以下任务并输出实施计划，不要执行代码:",
    "task_prompt_prefix": "你的任务是:",
    "task_timeout": 600,
}


@dataclass
class Config:
    max_workers: int = 2
    main_branch: str = "main"
    claude_args: List[str] = field(default_factory=list)
    auto_merge: bool = True
    merge_strategy: str = "--no-ff"
    worktree_dir: str = ".claude-flow/worktrees"
    skip_permissions: bool = True
    plan_prompt_prefix: str = "请分析以下任务并输出实施计划，不要执行代码:"
    task_prompt_prefix: str = "你的任务是:"
    task_timeout: int = 600

    @classmethod
    def load(cls, project_root: Path) -> Config:
        config_file = project_root / CLAUDE_FLOW_DIR / CONFIG_FILE
        if not config_file.exists():
            return cls()
        data = json.loads(config_file.read_text())
        merged = {**DEFAULT_CONFIG, **data}
        return cls(**{k: v for k, v in merged.items() if k in cls.__dataclass_fields__})

    def save(self, project_root: Path) -> None:
        config_file = project_root / CLAUDE_FLOW_DIR / CONFIG_FILE
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @staticmethod
    def claude_flow_dir(project_root: Path) -> Path:
        return project_root / CLAUDE_FLOW_DIR
```

**Step 4: Run tests**

```bash
pytest tests/test_config.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: config module with load/save and defaults"
```

---

### Task 3: TaskManager (CRUD + File Lock)

**Files:**
- Create: `claude_flow/task_manager.py`
- Create: `tests/test_task_manager.py`

**Step 1: Write failing tests**

```python
# tests/test_task_manager.py
import json
from pathlib import Path
from claude_flow.task_manager import TaskManager
from claude_flow.models import Task, TaskStatus


class TestTaskManager:
    def _make_manager(self, tmp_path: Path) -> TaskManager:
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        return TaskManager(tmp_path)

    def test_add_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add("Login API", "Implement login endpoint")
        assert task.title == "Login API"
        assert task.status == TaskStatus.PENDING
        tasks = mgr.list_tasks()
        assert len(tasks) == 1

    def test_list_empty(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.list_tasks() == []

    def test_get_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add("Test", "prompt")
        found = mgr.get(task.id)
        assert found is not None
        assert found.id == task.id

    def test_get_missing(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.get("nonexistent") is None

    def test_remove_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add("Test", "prompt")
        assert mgr.remove(task.id) is True
        assert mgr.list_tasks() == []

    def test_update_status(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        task = mgr.add("Test", "prompt")
        mgr.update_status(task.id, TaskStatus.APPROVED)
        updated = mgr.get(task.id)
        assert updated.status == TaskStatus.APPROVED

    def test_claim_task(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.add("T1", "p1")
        t2 = mgr.add("T2", "p2")
        mgr.update_status(t2.id, TaskStatus.APPROVED)
        claimed = mgr.claim_next(worker_id=0)
        assert claimed is not None
        assert claimed.id == t2.id
        assert claimed.status == TaskStatus.RUNNING
        assert claimed.worker_id == 0

    def test_claim_returns_none_when_empty(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        assert mgr.claim_next(worker_id=0) is None

    def test_add_from_file(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        tasks_file = tmp_path / "tasks.txt"
        tasks_file.write_text("Login | Implement login\nSignup | Implement signup\n")
        added = mgr.add_from_file(tasks_file)
        assert len(added) == 2
        assert added[0].title == "Login"
        assert added[1].prompt == "Implement signup"

    def test_persistence(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.add("Persist", "test persistence")
        mgr2 = TaskManager(tmp_path)
        assert len(mgr2.list_tasks()) == 1
```

**Step 2: Run tests to verify fail**

```bash
pytest tests/test_task_manager.py -v
```

**Step 3: Implement TaskManager**

```python
# claude_flow/task_manager.py
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

    def add(self, title: str, prompt: str) -> Task:
        def _do():
            tasks = self._load()
            task = Task(title=title, prompt=prompt)
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
            for t in tasks:
                if t.status == TaskStatus.APPROVED:
                    t.status = TaskStatus.RUNNING
                    t.worker_id = worker_id
                    t.started_at = datetime.now()
                    t.branch = f"cf/{t.id}"
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
                added.append(self.add(title.strip(), prompt.strip()))
            else:
                added.append(self.add(line, line))
        return added
```

**Step 4: Run tests**

```bash
pytest tests/test_task_manager.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: TaskManager with CRUD, file locking, and batch import"
```

---

### Task 4: Worktree Module

**Files:**
- Create: `claude_flow/worktree.py`
- Create: `tests/test_worktree.py`

**Step 1: Write failing tests**

```python
# tests/test_worktree.py
import subprocess
from pathlib import Path
from claude_flow.worktree import WorktreeManager


def _init_git_repo(path: Path) -> Path:
    """Helper: create a minimal git repo with one commit."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True)
    (path / "README.md").write_text("# Test")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True)
    return path


class TestWorktreeManager:
    def test_create_worktree(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)
        wt_path = mgr.create("task-001", "cf/task-001")
        assert wt_path.exists()
        assert (wt_path / "README.md").exists()

    def test_remove_worktree(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)
        wt_path = mgr.create("task-001", "cf/task-001")
        mgr.remove("task-001", "cf/task-001")
        assert not wt_path.exists()

    def test_merge_to_main(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)
        wt_path = mgr.create("task-001", "cf/task-001")
        # make a change in worktree
        (wt_path / "new_file.txt").write_text("hello")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "add file"], check=True, capture_output=True)
        success = mgr.merge("cf/task-001", "main")
        assert success is True

    def test_merge_conflict_returns_false(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)
        wt_path = mgr.create("task-001", "cf/task-001")
        # make conflicting changes
        (repo / "README.md").write_text("# Main change")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "main change"], check=True, capture_output=True)
        (wt_path / "README.md").write_text("# Branch change")
        subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "branch change"], check=True, capture_output=True)
        success = mgr.merge("cf/task-001", "main")
        assert success is False

    def test_list_worktrees(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        wt_dir = repo / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(repo, wt_dir)
        assert mgr.list_active() == []
        mgr.create("task-001", "cf/task-001")
        active = mgr.list_active()
        assert len(active) == 1
        assert active[0] == "task-001"
```

**Step 2: Run tests to verify fail**

```bash
pytest tests/test_worktree.py -v
```

**Step 3: Implement WorktreeManager**

```python
# claude_flow/worktree.py
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List


class WorktreeManager:
    def __init__(self, repo_root: Path, worktree_dir: Path):
        self._repo = repo_root
        self._wt_dir = worktree_dir

    def _run(self, args: List[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, cwd=cwd or self._repo,
            capture_output=True, text=True, check=check,
        )

    def create(self, task_id: str, branch: str) -> Path:
        wt_path = self._wt_dir / task_id
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["git", "worktree", "add", "-b", branch, str(wt_path)])
        return wt_path

    def remove(self, task_id: str, branch: str) -> None:
        wt_path = self._wt_dir / task_id
        self._run(["git", "worktree", "remove", str(wt_path), "--force"], check=False)
        self._run(["git", "branch", "-D", branch], check=False)

    def merge(self, branch: str, main_branch: str, strategy: str = "--no-ff") -> bool:
        try:
            self._run(["git", "checkout", main_branch])
            self._run(["git", "merge", strategy, branch, "-m", f"merge {branch}"])
            return True
        except subprocess.CalledProcessError:
            self._run(["git", "merge", "--abort"], check=False)
            self._run(["git", "checkout", main_branch], check=False)
            return False

    def list_active(self) -> List[str]:
        if not self._wt_dir.exists():
            return []
        return [d.name for d in self._wt_dir.iterdir() if d.is_dir()]

    def cleanup_all(self) -> int:
        count = 0
        for task_id in self.list_active():
            branch = f"cf/{task_id}"
            self.remove(task_id, branch)
            count += 1
        return count
```

**Step 4: Run tests**

```bash
pytest tests/test_worktree.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: WorktreeManager with create/remove/merge/cleanup"
```

---

### Task 5: Planner Module (Plan Mode)

**Files:**
- Create: `claude_flow/planner.py`
- Create: `tests/test_planner.py`

**Step 1: Write failing tests**

```python
# tests/test_planner.py
from pathlib import Path
from unittest.mock import patch, MagicMock
from claude_flow.planner import Planner
from claude_flow.models import Task, TaskStatus
from claude_flow.config import Config


class TestPlanner:
    def _make_planner(self, tmp_path: Path) -> Planner:
        plans_dir = tmp_path / ".claude-flow" / "plans"
        plans_dir.mkdir(parents=True)
        cfg = Config()
        return Planner(tmp_path, plans_dir, cfg)

    @patch("claude_flow.planner.subprocess.run")
    def test_generate_plan(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="# Plan\n1. Step one\n2. Step two"
        )
        planner = self._make_planner(tmp_path)
        task = Task(title="Test", prompt="Implement feature X")
        plan_file = planner.generate(task)
        assert plan_file.exists()
        assert "Step one" in plan_file.read_text()
        assert task.status == TaskStatus.PLANNED
        assert task.plan_file == str(plan_file)

    @patch("claude_flow.planner.subprocess.run")
    def test_generate_plan_failure(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        planner = self._make_planner(tmp_path)
        task = Task(title="Test", prompt="Bad task")
        plan_file = planner.generate(task)
        assert plan_file is None
        assert task.status == TaskStatus.FAILED

    def test_read_plan(self, tmp_path):
        planner = self._make_planner(tmp_path)
        plan_path = tmp_path / ".claude-flow" / "plans" / "task-001.md"
        plan_path.write_text("# My Plan\nDo stuff")
        content = planner.read_plan(plan_path)
        assert "My Plan" in content

    def test_approve(self, tmp_path):
        planner = self._make_planner(tmp_path)
        task = Task(title="Test", prompt="P")
        task.status = TaskStatus.PLANNED
        planner.approve(task)
        assert task.status == TaskStatus.APPROVED

    def test_reject_appends_reason(self, tmp_path):
        planner = self._make_planner(tmp_path)
        task = Task(title="Test", prompt="Original prompt")
        task.status = TaskStatus.PLANNED
        planner.reject(task, "需要更多错误处理")
        assert task.status == TaskStatus.PENDING
        assert "需要更多错误处理" in task.prompt
```

**Step 2: Run tests to verify fail**

```bash
pytest tests/test_planner.py -v
```

**Step 3: Implement Planner**

```python
# claude_flow/planner.py
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
```

**Step 4: Run tests**

```bash
pytest tests/test_planner.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: Planner module with generate/approve/reject workflow"
```

---

### Task 6: Worker Module

**Files:**
- Create: `claude_flow/worker.py`
- Create: `tests/test_worker.py`

**Step 1: Write failing tests**

```python
# tests/test_worker.py
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from claude_flow.worker import Worker
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import WorktreeManager
from claude_flow.config import Config
from claude_flow.models import TaskStatus


def _init_git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True, capture_output=True)
    (path / "README.md").write_text("# Test")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True)
    return path


class TestWorker:
    def _setup(self, tmp_path: Path):
        repo = _init_git_repo(tmp_path / "repo")
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir()
        logs_dir = cf_dir / "logs"
        logs_dir.mkdir()
        cfg = Config()
        tm = TaskManager(repo)
        wt = WorktreeManager(repo, cf_dir / "worktrees")
        worker = Worker(worker_id=0, project_root=repo, task_manager=tm, worktree_manager=wt, config=cfg)
        return repo, tm, wt, worker

    def test_worker_init(self, tmp_path):
        _, _, _, worker = self._setup(tmp_path)
        assert worker.worker_id == 0

    @patch("claude_flow.worker.subprocess.run")
    def test_execute_task_success(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="done")
        repo, tm, wt, worker = self._setup(tmp_path)
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(worker_id=0)
        result = worker.execute_task(claimed)
        assert result is True

    @patch("claude_flow.worker.subprocess.run")
    def test_execute_task_failure(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        repo, tm, wt, worker = self._setup(tmp_path)
        task = tm.add("Test", "prompt")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(worker_id=0)
        result = worker.execute_task(claimed)
        assert result is False

    def test_run_loop_no_tasks(self, tmp_path):
        _, tm, _, worker = self._setup(tmp_path)
        # should exit immediately with no approved tasks
        count = worker.run_loop()
        assert count == 0
```

**Step 2: Run tests to verify fail**

```bash
pytest tests/test_worker.py -v
```

**Step 3: Implement Worker**

```python
# claude_flow/worker.py
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
```

**Step 4: Run tests**

```bash
pytest tests/test_worker.py -v
```

Expected: All PASS

**Step 5: Commit**

```bash
git add -A && git commit -m "feat: Worker with execute/merge/cleanup loop"
```

---

### Task 7: CLI Entry Point (Click)

**Files:**
- Create: `claude_flow/cli.py`
- Create: `tests/test_cli.py`

**Step 1: Write failing tests**

```python
# tests/test_cli.py
import subprocess
from pathlib import Path
from click.testing import CliRunner
from claude_flow.cli import main


def _init_git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True, capture_output=True)
    (path / "README.md").write_text("# Test")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True)
    return path


class TestCLI:
    def test_init(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        runner = CliRunner()
        result = runner.invoke(main, ["init"], catch_exceptions=False, env={"CF_PROJECT_ROOT": str(repo)})
        assert result.exit_code == 0
        assert (repo / ".claude-flow").is_dir()
        assert (repo / ".claude-flow" / "config.json").exists()

    def test_task_add(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        (repo / ".claude-flow").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main, ["task", "add", "-p", "Do something", "My Task"],
            catch_exceptions=False, env={"CF_PROJECT_ROOT": str(repo)},
        )
        assert result.exit_code == 0
        assert "My Task" in result.output

    def test_task_list_empty(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        (repo / ".claude-flow").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main, ["task", "list"],
            catch_exceptions=False, env={"CF_PROJECT_ROOT": str(repo)},
        )
        assert result.exit_code == 0

    def test_status(self, tmp_path):
        repo = _init_git_repo(tmp_path / "repo")
        (repo / ".claude-flow").mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main, ["status"],
            catch_exceptions=False, env={"CF_PROJECT_ROOT": str(repo)},
        )
        assert result.exit_code == 0
```

**Step 2: Run tests to verify fail**

```bash
pytest tests/test_cli.py -v
```

**Step 3: Implement CLI**

```python
# claude_flow/cli.py
from __future__ import annotations

import logging
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional

import click

from .config import Config
from .models import TaskStatus
from .planner import Planner
from .task_manager import TaskManager
from .worker import Worker
from .worktree import WorktreeManager


def _get_root() -> Path:
    env_root = os.environ.get("CF_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    # walk up to find .claude-flow or .git
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".claude-flow").exists() or (parent / ".git").exists():
            return parent
    return cwd


@click.group()
@click.pass_context
def main(ctx):
    """Claude Flow - Multi-instance Claude Code workflow manager."""
    ctx.ensure_object(dict)
    ctx.obj["root"] = _get_root()


@main.command()
@click.pass_context
def init(ctx):
    """Initialize .claude-flow/ in the current project."""
    root = ctx.obj["root"]
    cf_dir = root / ".claude-flow"
    for sub in ["logs", "plans", "worktrees"]:
        (cf_dir / sub).mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cfg.save(root)
    # Add .claude-flow/worktrees and lock/log files to .gitignore
    gitignore = root / ".gitignore"
    ignore_lines = [".claude-flow/worktrees/", ".claude-flow/tasks.lock", ".claude-flow/logs/"]
    existing = gitignore.read_text() if gitignore.exists() else ""
    to_add = [l for l in ignore_lines if l not in existing]
    if to_add:
        with open(gitignore, "a") as f:
            f.write("\n# claude-flow\n" + "\n".join(to_add) + "\n")
    click.echo(f"Initialized .claude-flow/ in {root}")


# ── Task commands ──────────────────────────────────────────────

@main.group()
def task():
    """Manage tasks."""
    pass


@task.command("add")
@click.argument("title")
@click.option("-p", "--prompt", default=None, help="Task prompt for Claude Code")
@click.option("-f", "--file", "filepath", default=None, type=click.Path(exists=True), help="Import tasks from file")
@click.pass_context
def task_add(ctx, title, prompt, filepath):
    """Add a new task."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    if filepath:
        added = tm.add_from_file(Path(filepath))
        click.echo(f"Added {len(added)} tasks")
        return
    if prompt is None:
        prompt = click.edit("# Enter the task prompt for Claude Code\n")
        if not prompt:
            click.echo("Aborted: no prompt provided")
            return
    t = tm.add(title, prompt)
    click.echo(f"Added: {t.id} - {t.title}")


@task.command("list")
@click.pass_context
def task_list(ctx):
    """List all tasks."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    tasks = tm.list_tasks()
    if not tasks:
        click.echo("No tasks")
        return
    for t in tasks:
        status_icon = {"pending": "○", "planning": "⟳", "planned": "◉", "approved": "✓",
                       "running": "▶", "merging": "⇄", "done": "●", "failed": "✗"}
        icon = status_icon.get(t.status.value, "?")
        click.echo(f"  {icon} {t.id}  {t.status.value:<10}  {t.title}")


@task.command("show")
@click.argument("task_id")
@click.pass_context
def task_show(ctx, task_id):
    """Show task details."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    t = tm.get(task_id)
    if not t:
        click.echo(f"Task {task_id} not found")
        return
    click.echo(f"ID:      {t.id}")
    click.echo(f"Title:   {t.title}")
    click.echo(f"Status:  {t.status.value}")
    click.echo(f"Branch:  {t.branch or '-'}")
    click.echo(f"Worker:  {t.worker_id or '-'}")
    click.echo(f"Created: {t.created_at}")
    if t.error:
        click.echo(f"Error:   {t.error}")
    click.echo(f"\nPrompt:\n{t.prompt}")


@task.command("remove")
@click.argument("task_id")
@click.pass_context
def task_remove(ctx, task_id):
    """Remove a task."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    if tm.remove(task_id):
        click.echo(f"Removed {task_id}")
    else:
        click.echo(f"Task {task_id} not found")


# ── Plan commands ──────────────────────────────────────────────

@main.group(invoke_without_command=True)
@click.argument("task_id", required=False)
@click.pass_context
def plan(ctx, task_id):
    """Generate plans for pending tasks."""
    if ctx.invoked_subcommand is not None:
        return
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg)

    if task_id:
        tasks = [tm.get(task_id)]
        if tasks[0] is None:
            click.echo(f"Task {task_id} not found")
            return
    else:
        tasks = tm.list_tasks(status=TaskStatus.PENDING)

    if not tasks:
        click.echo("No pending tasks to plan")
        return

    for t in tasks:
        click.echo(f"Planning: {t.id} - {t.title} ...")
        plan_file = planner.generate(t)
        if plan_file:
            tm.update_status(t.id, TaskStatus.PLANNED)
            # store plan_file path
            click.echo(f"  ✓ Plan saved to {plan_file}")
        else:
            tm.update_status(t.id, TaskStatus.FAILED, t.error)
            click.echo(f"  ✗ Plan failed: {t.error}")


@plan.command("review")
@click.pass_context
def plan_review(ctx):
    """Interactively review generated plans."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    plans_dir = root / ".claude-flow" / "plans"
    planner = Planner(root, plans_dir, cfg)

    tasks = tm.list_tasks(status=TaskStatus.PLANNED)
    if not tasks:
        click.echo("No plans to review")
        return

    for t in tasks:
        plan_path = Path(t.plan_file) if t.plan_file else plans_dir / f"{t.id}.md"
        if not plan_path.exists():
            click.echo(f"Plan file missing for {t.id}, skipping")
            continue

        click.echo(f"\n{'─' * 50}")
        click.echo(f"Task:   {t.id} - {t.title}")
        click.echo(f"{'─' * 50}")
        click.echo(planner.read_plan(plan_path))
        click.echo(f"{'─' * 50}")

        action = click.prompt("[a]pprove  [r]eject  [s]kip  [e]dit  [q]uit", type=str, default="s")
        if action == "a":
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"✓ {t.id} approved")
        elif action == "r":
            reason = click.prompt("Rejection reason", default="")
            planner.reject(t, reason)
            tm.update_status(t.id, TaskStatus.PENDING)
            click.echo(f"↩ {t.id} rejected, back to pending")
        elif action == "e":
            editor = os.environ.get("EDITOR", "vi")
            subprocess.run([editor, str(plan_path)])
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"✓ {t.id} edited and approved")
        elif action == "q":
            break


@plan.command("approve")
@click.argument("task_id", required=False)
@click.option("--all", "approve_all", is_flag=True, help="Approve all planned tasks")
@click.pass_context
def plan_approve(ctx, task_id, approve_all):
    """Approve a plan or all plans."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    cfg = Config.load(root)
    planner = Planner(root, root / ".claude-flow" / "plans", cfg)

    if approve_all:
        tasks = tm.list_tasks(status=TaskStatus.PLANNED)
        for t in tasks:
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"✓ {t.id} approved")
    elif task_id:
        t = tm.get(task_id)
        if t and t.status == TaskStatus.PLANNED:
            planner.approve(t)
            tm.update_status(t.id, TaskStatus.APPROVED)
            click.echo(f"✓ {t.id} approved")
        else:
            click.echo(f"Task {task_id} not found or not in planned state")


# ── Run command ────────────────────────────────────────────────

@main.command()
@click.option("-n", "--num-workers", default=1, type=int, help="Number of parallel workers")
@click.argument("task_id", required=False)
@click.pass_context
def run(ctx, num_workers, task_id):
    """Start workers to execute approved tasks."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    tm = TaskManager(root)
    wt = WorktreeManager(root, root / cfg.worktree_dir)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if task_id:
        t = tm.get(task_id)
        if not t:
            click.echo(f"Task {task_id} not found")
            return
        if t.status != TaskStatus.APPROVED:
            tm.update_status(t.id, TaskStatus.APPROVED)
        worker = Worker(0, root, tm, wt, cfg)
        t = tm.claim_next(0)
        if t:
            worker.execute_task(t)
        return

    if num_workers == 1:
        worker = Worker(0, root, tm, wt, cfg)
        count = worker.run_loop()
        click.echo(f"Completed {count} tasks")
    else:
        # Multi-worker: spawn subprocesses
        import multiprocessing

        def _worker_entry(wid):
            w = Worker(wid, root, tm, wt, cfg)
            return w.run_loop()

        with multiprocessing.Pool(num_workers) as pool:
            results = pool.map(_worker_entry, range(num_workers))
        total = sum(results)
        click.echo(f"Completed {total} tasks across {num_workers} workers")


# ── Status / Log / Clean / Reset / Retry ───────────────────────

@main.command()
@click.pass_context
def status(ctx):
    """Show task and worker status overview."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    tasks = tm.list_tasks()
    counts = {}
    for t in tasks:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1
    click.echo(f"Total tasks: {len(tasks)}")
    for s, c in sorted(counts.items()):
        click.echo(f"  {s}: {c}")


@main.command()
@click.argument("task_id")
@click.pass_context
def log(ctx, task_id):
    """View task execution log."""
    root = ctx.obj["root"]
    log_file = root / ".claude-flow" / "logs" / f"{task_id}.log"
    if log_file.exists():
        click.echo(log_file.read_text())
    else:
        click.echo(f"No log for {task_id}")


@main.command()
@click.pass_context
def clean(ctx):
    """Clean up worktrees and merged branches."""
    root = ctx.obj["root"]
    cfg = Config.load(root)
    wt = WorktreeManager(root, root / cfg.worktree_dir)
    count = wt.cleanup_all()
    click.echo(f"Cleaned {count} worktrees")


@main.command()
@click.argument("task_id")
@click.pass_context
def reset(ctx, task_id):
    """Reset a failed task back to pending."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    t = tm.get(task_id)
    if t and t.status == TaskStatus.FAILED:
        tm.update_status(task_id, TaskStatus.PENDING)
        click.echo(f"Reset {task_id} to pending")
    else:
        click.echo(f"Task {task_id} not found or not failed")


@main.command()
@click.pass_context
def retry(ctx):
    """Retry all failed tasks."""
    root = ctx.obj["root"]
    tm = TaskManager(root)
    failed = tm.list_tasks(status=TaskStatus.FAILED)
    for t in failed:
        tm.update_status(t.id, TaskStatus.APPROVED)
        click.echo(f"↻ {t.id} → approved")
    click.echo(f"Retrying {len(failed)} tasks")
```

**Step 4: Run tests**

```bash
pytest tests/test_cli.py -v
```

Expected: All PASS

**Step 5: Run full test suite**

```bash
pytest --tb=short -v
```

Expected: All tests pass

**Step 6: Install and smoke test**

```bash
pip install -e .
# Test in a temp git repo
cd /tmp && mkdir cf-test && cd cf-test && git init && git config user.name "test" && git config user.email "test@test.com" && git commit --allow-empty -m "init"
cf init
cf task add -p "Create a hello world Python script" "Hello World"
cf task list
cf status
```

**Step 7: Commit**

```bash
git add -A && git commit -m "feat: complete CLI with all commands (init, task, plan, run, status, clean, reset, retry)"
```

---

## Task Summary

| Task | Description | Est. Time |
|------|-------------|-----------|
| 1 | Project scaffolding + models | 5 min |
| 2 | Config module | 5 min |
| 3 | TaskManager (CRUD + file lock) | 10 min |
| 4 | WorktreeManager | 10 min |
| 5 | Planner (plan mode) | 10 min |
| 6 | Worker (execute + merge loop) | 10 min |
| 7 | CLI entry point (Click) | 15 min |

**Total estimated: ~65 minutes**

## Execution Order

Tasks 1 → 2 → 3 → 4 → 5 → 6 → 7 (sequential, each builds on the previous)
