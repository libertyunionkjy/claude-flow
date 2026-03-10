# Git Submodule Support Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Claude Flow to work with Git submodules — initializing target submodules in worktrees, two-step committing (submodule then main project), and exposing submodule selection via CLI/Web API.

**Architecture:** Extend existing Task model with `submodules: List[str]` field. WorktreeManager gains `_init_submodules()` called during `create()`. Worker's `_auto_commit()` gets two-step logic: commit inside each submodule first, then commit the main project (capturing pointer updates). CLI adds `-s/--submodule` to `task add` and `task mini`. Web API adds `GET /api/submodules` and accepts `submodules` field in task creation endpoints.

**Tech Stack:** Python 3.10+, Click, Flask, pytest, Git CLI

**Spec:** `docs/superpowers/specs/2026-03-10-submodule-support-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `claude_flow/models.py` | Add `submodules` field to Task, update serialization |
| Modify | `claude_flow/worktree.py` | Add `_init_submodules()`, extend `create()` |
| Modify | `claude_flow/worker.py` | Two-step `_auto_commit()` for submodule + main project |
| Modify | `claude_flow/task_manager.py` | Pass `submodules` through `add()` and `add_mini()` |
| Modify | `claude_flow/cli.py` | Add `-s/--submodule` to `task add` and `task mini` |
| Modify | `claude_flow/web/api.py` | Accept `submodules` in create, add `GET /api/submodules` |
| Create | `tests/unit/test_submodule.py` | All submodule unit tests |
| Modify | `tests/conftest.py` | Add `git_repo_with_submodule` fixture |

---

## Chunk 1: Data Model & Serialization

### Task 1: Task model — add `submodules` field

**Files:**
- Modify: `claude_flow/models.py:34-96`
- Test: `tests/unit/test_submodule.py` (create new)

- [ ] **Step 1: Write failing tests for Task.submodules**

Create `tests/unit/test_submodule.py`:

```python
"""Tests for Git submodule support."""
from datetime import datetime

from claude_flow.models import Task, TaskStatus


class TestTaskSubmodules:
    def test_task_default_submodules_empty(self):
        """Task should have empty submodules list by default."""
        task = Task(title="Test", prompt="prompt")
        assert task.submodules == []

    def test_task_with_submodules(self):
        """Task should accept submodules list."""
        task = Task(title="Test", prompt="prompt", submodules=["libs/core", "libs/ui"])
        assert task.submodules == ["libs/core", "libs/ui"]

    def test_task_to_dict_includes_submodules(self):
        """to_dict should include submodules field."""
        task = Task(title="Test", prompt="prompt", submodules=["libs/core"])
        d = task.to_dict()
        assert d["submodules"] == ["libs/core"]

    def test_task_to_dict_empty_submodules(self):
        """to_dict should include empty submodules list."""
        task = Task(title="Test", prompt="prompt")
        d = task.to_dict()
        assert d["submodules"] == []

    def test_task_from_dict_with_submodules(self):
        """from_dict should restore submodules."""
        d = {
            "id": "task-001",
            "title": "Test",
            "prompt": "prompt",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "submodules": ["libs/core", "libs/ui"],
        }
        task = Task.from_dict(d)
        assert task.submodules == ["libs/core", "libs/ui"]

    def test_task_from_dict_without_submodules_backward_compat(self):
        """from_dict should default to empty list when submodules key is missing (backward compat)."""
        d = {
            "id": "task-001",
            "title": "Test",
            "prompt": "prompt",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
        task = Task.from_dict(d)
        assert task.submodules == []

    def test_task_roundtrip_with_submodules(self):
        """Serialization roundtrip should preserve submodules."""
        task = Task(title="Roundtrip", prompt="p", submodules=["a/b", "c/d"])
        restored = Task.from_dict(task.to_dict())
        assert restored.submodules == ["a/b", "c/d"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_submodule.py -v`
Expected: FAIL — `Task.__init__()` got unexpected keyword argument 'submodules'

- [ ] **Step 3: Add submodules field to Task dataclass**

In `claude_flow/models.py`, add after `plan_mode` field (line 50):

```python
    submodules: list[str] = field(default_factory=list)
```

Update `to_dict()` — add inside the return dict:

```python
            "submodules": self.submodules,
```

Update `from_dict()` — add to the constructor call:

```python
            submodules=d.get("submodules", []),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_submodule.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Run full test suite to check backward compatibility**

Run: `pytest tests/ -v`
Expected: All existing tests still PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add claude_flow/models.py tests/unit/test_submodule.py
git commit -m "feat(models): add submodules field to Task dataclass"
```

---

### Task 2: TaskManager — pass submodules through add/add_mini

**Files:**
- Modify: `claude_flow/task_manager.py:100-123`
- Test: `tests/unit/test_submodule.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_submodule.py`:

```python
from claude_flow.task_manager import TaskManager


class TestTaskManagerSubmodules:
    def test_add_with_submodules(self, tmp_path):
        """TaskManager.add should pass submodules to Task."""
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add("Test", "prompt", submodules=["libs/core"])
        assert task.submodules == ["libs/core"]
        # Verify persisted
        loaded = tm.get(task.id)
        assert loaded.submodules == ["libs/core"]

    def test_add_mini_with_submodules(self, tmp_path):
        """TaskManager.add_mini should pass submodules to Task."""
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add_mini("Test", "prompt", submodules=["libs/ui"])
        assert task.submodules == ["libs/ui"]
        loaded = tm.get(task.id)
        assert loaded.submodules == ["libs/ui"]

    def test_add_without_submodules_default(self, tmp_path):
        """TaskManager.add without submodules should default to empty list."""
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tm = TaskManager(tmp_path)
        task = tm.add("Test", "prompt")
        assert task.submodules == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_submodule.py::TestTaskManagerSubmodules -v`
Expected: FAIL — `add()` got unexpected keyword argument 'submodules'

- [ ] **Step 3: Update TaskManager.add and add_mini**

In `claude_flow/task_manager.py`, modify `add()` (line 100):

```python
    def add(self, title: str, prompt: str, priority: int = 0,
            submodules: list[str] | None = None) -> Task:
        def _do():
            tasks = self._load()
            task = Task(title=title, prompt=prompt, priority=priority,
                        submodules=submodules or [])
            tasks.append(task)
            self._save(tasks)
            return task
        return self._with_lock(_do)
```

Modify `add_mini()` (line 109):

```python
    def add_mini(self, title: str, prompt: str, priority: int = 0,
                 submodules: list[str] | None = None) -> Task:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_submodule.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: No regressions

- [ ] **Step 6: Commit**

```bash
git add claude_flow/task_manager.py tests/unit/test_submodule.py
git commit -m "feat(task_manager): support submodules param in add/add_mini"
```

---

## Chunk 2: WorktreeManager — Submodule Initialization

### Task 3: Add git_repo_with_submodule fixture

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def git_repo_with_submodule(tmp_path: Path) -> dict:
    """Create a git repo with a submodule and return paths.

    Returns dict with keys: repo, sub_repo, sub_path.
    - repo: main project path
    - sub_repo: bare submodule repo path (acts as remote)
    - sub_path: relative submodule path inside main project ("libs/mylib")
    """
    # Create a bare repo to serve as the submodule "remote"
    sub_remote = tmp_path / "sub_remote"
    subprocess.run(["git", "init", "-b", "main", str(sub_remote)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sub_remote), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sub_remote), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    (sub_remote / "lib.py").write_text("# library code\ndef hello():\n    return 'hello'\n")
    subprocess.run(["git", "-C", str(sub_remote), "add", "."],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sub_remote), "commit", "-m", "init lib"],
                   check=True, capture_output=True)

    # Create main repo
    repo = tmp_path / "main_project"
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    (repo / "README.md").write_text("# Main Project")
    subprocess.run(["git", "-C", str(repo), "add", "."],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init main"],
                   check=True, capture_output=True)

    # Add submodule
    sub_path = "libs/mylib"
    subprocess.run(
        ["git", "-C", str(repo), "submodule", "add", str(sub_remote), sub_path],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add submodule"],
                   check=True, capture_output=True)

    return {"repo": repo, "sub_remote": sub_remote, "sub_path": sub_path}
```

- [ ] **Step 2: Verify fixture works**

Run: `python -c "print('fixture defined')"` — just verify no syntax errors by running:
`pytest tests/conftest.py --collect-only`
Expected: Fixture registered without errors

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test(conftest): add git_repo_with_submodule fixture"
```

---

### Task 4: WorktreeManager._init_submodules and create() extension

**Files:**
- Modify: `claude_flow/worktree.py:155-176`
- Test: `tests/unit/test_submodule.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_submodule.py`:

```python
import subprocess
from pathlib import Path
from claude_flow.worktree import WorktreeManager


class TestWorktreeSubmoduleInit:
    def test_create_with_submodule_initializes_submodule(self, git_repo_with_submodule):
        """Worktree creation with submodules should init the specified submodule."""
        info = git_repo_with_submodule
        repo, sub_path = info["repo"], info["sub_path"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)
        mgr = WorktreeManager(repo, wt_dir)

        wt_path = mgr.create("task-sub1", "cf/task-sub1", submodules=[sub_path])

        # Submodule should be initialized in worktree
        sub_in_wt = wt_path / sub_path
        assert sub_in_wt.exists()
        assert (sub_in_wt / "lib.py").exists()

    def test_create_without_submodule_leaves_empty(self, git_repo_with_submodule):
        """Worktree creation without submodules should not init submodules."""
        info = git_repo_with_submodule
        repo, sub_path = info["repo"], info["sub_path"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)
        mgr = WorktreeManager(repo, wt_dir)

        wt_path = mgr.create("task-nosub", "cf/task-nosub")

        # Submodule directory should exist but be empty (not initialized)
        sub_in_wt = wt_path / sub_path
        # The directory may or may not exist, but lib.py should not
        assert not (sub_in_wt / "lib.py").exists()

    def test_create_with_invalid_submodule_raises(self, git_repo_with_submodule):
        """Worktree creation with invalid submodule path should raise."""
        info = git_repo_with_submodule
        repo = info["repo"]
        wt_dir = repo / ".claude-flow" / "worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)
        mgr = WorktreeManager(repo, wt_dir)

        with pytest.raises(subprocess.CalledProcessError):
            mgr.create("task-bad", "cf/task-bad", submodules=["nonexistent/path"])

    def test_create_non_git_ignores_submodules(self, non_git_dir):
        """Non-git mode should ignore submodules param."""
        wt_dir = non_git_dir / ".claude-flow" / "worktrees"
        mgr = WorktreeManager(non_git_dir, wt_dir, is_git=False)
        result = mgr.create("task-ng", "cf/task-ng", submodules=["libs/core"])
        assert result == non_git_dir  # Returns project root, no crash
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_submodule.py::TestWorktreeSubmoduleInit -v`
Expected: FAIL — `create()` got unexpected keyword argument 'submodules'

- [ ] **Step 3: Implement _init_submodules and extend create()**

In `claude_flow/worktree.py`, add method after `_setup_symlinks` (around line 150):

```python
    def _init_submodules(self, wt_path: Path, submodules: list[str]) -> None:
        """在 worktree 中选择性初始化指定的 submodule。

        只初始化任务指定的 submodule，不触碰其他 submodule。
        利用主仓库 .git/modules/ 共享对象存储，update 本质是 checkout。
        初始化失败时抛出 CalledProcessError，由调用方处理。
        """
        for sub_path in submodules:
            self._run(["git", "submodule", "init", sub_path], cwd=wt_path)
            self._run(["git", "submodule", "update", sub_path], cwd=wt_path)
```

Modify `create()` signature and body:

```python
    def create(self, task_id: str, branch: str, config: Config = None,
               submodules: list[str] | None = None) -> Path:
        """创建 worktree 并设置 symlink 共享文件。

        Non-git mode: returns the project root directly (no isolation).
        """
        if not self._is_git:
            return self._repo

        wt_path = self._wt_dir / task_id
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["git", "worktree", "add", "-b", branch, str(wt_path)])

        # 如果提供了 config，设置 symlink
        if config is not None:
            self._setup_symlinks(
                wt_path,
                shared=config.shared_symlinks,
                forbidden=config.forbidden_symlinks,
            )

        # 初始化指定的 submodule
        if submodules:
            self._init_submodules(wt_path, submodules)

        return wt_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_submodule.py::TestWorktreeSubmoduleInit -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: No regressions (existing `create()` calls don't pass `submodules`, defaults to `None`)

- [ ] **Step 6: Commit**

```bash
git add claude_flow/worktree.py tests/unit/test_submodule.py
git commit -m "feat(worktree): selective submodule init in worktree creation"
```

---

## Chunk 3: Worker — Two-Step Auto Commit

### Task 5: Worker._auto_commit two-step logic for submodules

**Files:**
- Modify: `claude_flow/worker.py:552-576`
- Modify: `claude_flow/worker.py:121-128` (pass submodules to worktree create)
- Test: `tests/unit/test_submodule.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_submodule.py`:

```python
from claude_flow.worker import Worker
from claude_flow.config import Config


class TestWorkerSubmoduleCommit:
    def _setup_worker(self, repo: Path):
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        (cf_dir / "logs").mkdir(exist_ok=True)
        cfg = Config()
        tm = TaskManager(repo)
        wt_dir = cf_dir / "worktrees"
        wt_dir.mkdir(exist_ok=True)
        wt = WorktreeManager(repo, wt_dir)
        worker = Worker(worker_id=0, project_root=repo,
                        task_manager=tm, worktree_manager=wt, config=cfg)
        return tm, wt, worker

    def test_auto_commit_submodule_then_main(self, git_repo_with_submodule):
        """Two-step commit: submodule first, then main project."""
        info = git_repo_with_submodule
        repo, sub_path = info["repo"], info["sub_path"]
        tm, wt, worker = self._setup_worker(repo)

        # Create task with submodule
        task = tm.add("Test sub commit", "modify submodule",
                      submodules=[sub_path])
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        # Create worktree with submodule
        wt_path = wt.create(claimed.id, claimed.branch,
                            submodules=claimed.submodules)

        # Simulate changes in submodule
        sub_in_wt = wt_path / sub_path
        (sub_in_wt / "lib.py").write_text("# modified\ndef hello():\n    return 'world'\n")

        # Run auto_commit
        result = worker._auto_commit(claimed, wt_path)
        assert result is True

        # Verify: submodule should have its own commit
        sub_log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(sub_in_wt), capture_output=True, text=True,
        )
        assert claimed.id in sub_log.stdout

        # Verify: main project should have a commit with updated pointer
        main_log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        assert claimed.id in main_log.stdout

    def test_auto_commit_no_submodule_changes(self, git_repo_with_submodule):
        """If submodule has no changes, skip submodule commit; main commit still works."""
        info = git_repo_with_submodule
        repo, sub_path = info["repo"], info["sub_path"]
        tm, wt, worker = self._setup_worker(repo)

        task = tm.add("No sub change", "only main change",
                      submodules=[sub_path])
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        wt_path = wt.create(claimed.id, claimed.branch,
                            submodules=claimed.submodules)

        # Only change main project file, not submodule
        (wt_path / "main_change.txt").write_text("main only")

        result = worker._auto_commit(claimed, wt_path)
        assert result is True

    def test_auto_commit_empty_submodules_list(self, git_repo_with_submodule):
        """Task with empty submodules list should use original commit logic."""
        info = git_repo_with_submodule
        repo = info["repo"]
        tm, wt, worker = self._setup_worker(repo)

        task = tm.add("No submodules", "normal task")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        wt_path = wt.create(claimed.id, claimed.branch)
        (wt_path / "file.txt").write_text("content")

        result = worker._auto_commit(claimed, wt_path)
        assert result is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_submodule.py::TestWorkerSubmoduleCommit -v`
Expected: FAIL — `add()` might pass but `_auto_commit` won't handle submodule dirs correctly

- [ ] **Step 3: Update _auto_commit for two-step logic**

In `claude_flow/worker.py`, replace `_auto_commit` method (line 552):

```python
    def _auto_commit(self, task: Task, wt_path: Path) -> bool:
        """检查 worktree 中是否有未提交的变更，如有则自动提交。

        对于带 submodule 的任务，执行两步提交：
        1. 先在每个 submodule 中独立提交
        2. 再在主项目中提交（捕获 submodule 指针更新）

        返回 True 表示有变更并已提交，False 表示无变更。
        """
        prefix = self._log_prefix()

        # 步骤 1: 对每个 submodule 单独提交
        for sub_path in task.submodules:
            sub_dir = wt_path / sub_path
            if not sub_dir.exists():
                continue
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(sub_dir), capture_output=True, text=True,
            )
            if status_result.stdout.strip():
                logger.info(f"{prefix} Auto-committing submodule {sub_path} for {task.id}")
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=str(sub_dir), capture_output=True, text=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"feat({task.id}): {task.title}",
                     "--no-verify"],
                    cwd=str(sub_dir), capture_output=True, text=True,
                )

        # 步骤 2: 主项目提交（包含 submodule 指针更新 + 其他改动）
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        if not status_result.stdout.strip():
            return False

        logger.info(f"{prefix} Auto-committing changes for {task.id}")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"feat({task.id}): {task.title}",
             "--no-verify"],
            cwd=str(wt_path), capture_output=True, text=True,
        )
        return True
```

- [ ] **Step 4: Update _execute_task_git to pass submodules to worktree create**

In `claude_flow/worker.py`, modify the `create()` call in `_execute_task_git` (around line 127):

```python
            wt_path = self._wt.create(task.id, task.branch, config=self._cfg,
                                      submodules=task.submodules or None)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_submodule.py::TestWorkerSubmoduleCommit -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: No regressions

- [ ] **Step 7: Commit**

```bash
git add claude_flow/worker.py tests/unit/test_submodule.py
git commit -m "feat(worker): two-step auto_commit for submodule tasks"
```

---

## Chunk 4: CLI — `-s/--submodule` Parameter

### Task 6: CLI task add and task mini submodule parameter

**Files:**
- Modify: `claude_flow/cli.py:117-179`
- Test: `tests/unit/test_submodule.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_submodule.py`:

```python
from unittest.mock import patch
from click.testing import CliRunner
from claude_flow.cli import main


class TestCliSubmodule:
    def test_task_add_with_submodule(self, git_repo_with_submodule):
        """cf task add -s <path> should create task with submodules."""
        info = git_repo_with_submodule
        repo = info["repo"]
        # Init .claude-flow
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "add", "Test Task",
                "-p", "do something",
                "-s", "libs/mylib",
            ])
            assert result.exit_code == 0
            assert "Added" in result.output

        # Verify task has submodule
        tm = TaskManager(repo)
        tasks = tm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].submodules == ["libs/mylib"]

    def test_task_add_multiple_submodules(self, git_repo_with_submodule):
        """cf task add -s a -s b should create task with multiple submodules."""
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "add", "Multi Sub",
                "-p", "prompt",
                "-s", "libs/a",
                "-s", "libs/b",
            ])
            assert result.exit_code == 0

        tm = TaskManager(repo)
        tasks = tm.list_tasks()
        assert set(tasks[0].submodules) == {"libs/a", "libs/b"}

    def test_task_add_without_submodule(self, git_repo_with_submodule):
        """cf task add without -s should have empty submodules."""
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "add", "Normal Task", "-p", "prompt",
            ])
            assert result.exit_code == 0

        tm = TaskManager(repo)
        tasks = tm.list_tasks()
        assert tasks[0].submodules == []

    def test_task_mini_with_submodule(self, git_repo_with_submodule):
        """cf task mini -s <path> should create mini task with submodules."""
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, [
                "task", "mini", "fix bug",
                "-s", "libs/mylib",
            ])
            assert result.exit_code == 0

        tm = TaskManager(repo)
        tasks = tm.list_tasks()
        assert tasks[0].submodules == ["libs/mylib"]

    def test_task_show_displays_submodules(self, git_repo_with_submodule):
        """cf task show should display submodules info."""
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        Config().save(repo)

        tm = TaskManager(repo)
        task = tm.add("Test", "prompt", submodules=["libs/mylib"])

        runner = CliRunner()
        with patch("claude_flow.cli._get_root", return_value=repo), \
             patch("claude_flow.cli.is_git_repo", return_value=True):
            result = runner.invoke(main, ["task", "show", task.id])
            assert result.exit_code == 0
            assert "libs/mylib" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_submodule.py::TestCliSubmodule -v`
Expected: FAIL — no `-s` option recognized

- [ ] **Step 3: Add -s/--submodule to CLI commands**

In `claude_flow/cli.py`, modify `task_add`:

```python
@task.command("add")
@click.argument("title")
@click.option("-p", "--prompt", default=None, help="Task prompt for Claude Code")
@click.option("-f", "--file", "filepath", default=None, type=click.Path(exists=True), help="Import tasks from file")
@click.option("-P", "--priority", default=0, type=int, help="Task priority (higher = more important)")
@click.option("-s", "--submodule", "submodules", multiple=True, help="Target submodule path (repeatable)")
@click.pass_context
def task_add(ctx, title, prompt, filepath, priority, submodules):
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
    t = tm.add(title, prompt, priority=priority, submodules=list(submodules))
    click.echo(f"Added: {t.id} - {t.title} (priority: {priority})")
```

Modify `task_mini`:

```python
@task.command("mini")
@click.argument("prompt")
@click.option("-t", "--title", default=None, help="Task title (defaults to truncated prompt)")
@click.option("-P", "--priority", default=0, type=int, help="Task priority (higher = more important)")
@click.option("-s", "--submodule", "submodules", multiple=True, help="Target submodule path (repeatable)")
@click.option("--run", "auto_run", is_flag=True, help="Immediately start a worker to execute")
@click.pass_context
def task_mini(ctx, prompt, title, priority, submodules, auto_run):
    # ... docstring unchanged ...
    root = ctx.obj["root"]
    tm = TaskManager(root)
    if title is None:
        title = prompt[:60] + ("..." if len(prompt) > 60 else "")
    t = tm.add_mini(title, prompt, priority=priority, submodules=list(submodules))
    click.echo(f"Mini task added: {t.id} - {t.title} [approved]")
    # ... rest unchanged ...
```

Modify `task_show` to display submodules (add after the "Worker" line):

```python
    if t.submodules:
        click.echo(f"Submodules: {', '.join(t.submodules)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_submodule.py::TestCliSubmodule -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: No regressions

- [ ] **Step 6: Commit**

```bash
git add claude_flow/cli.py tests/unit/test_submodule.py
git commit -m "feat(cli): add -s/--submodule to task add and task mini"
```

---

## Chunk 5: Web API — Submodule Endpoints

### Task 7: Web API submodules support

**Files:**
- Modify: `claude_flow/web/api.py:74-102,982-998,1001-1058`
- Test: `tests/unit/test_submodule.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_submodule.py`:

```python
import json


class TestWebApiSubmodule:
    def _create_app(self, repo):
        """Create Flask test app with required config."""
        from flask import Flask
        from claude_flow.web.api import api_bp
        from claude_flow.chat import ChatManager

        cfg = Config()
        cfg.save(repo)
        tm = TaskManager(repo)
        app = Flask(__name__)
        app.register_blueprint(api_bp)
        app.config["TASK_MANAGER"] = tm
        app.config["CF_CONFIG"] = cfg
        app.config["PROJECT_ROOT"] = repo
        app.config["IS_GIT"] = True
        app.config["CHAT_MANAGER"] = ChatManager(repo, cfg)
        return app, tm

    def test_create_task_with_submodules(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        (repo / ".claude-flow").mkdir(exist_ok=True)
        app, tm = self._create_app(repo)

        with app.test_client() as client:
            resp = client.post("/api/tasks", json={
                "title": "Test",
                "prompt": "do it",
                "submodules": ["libs/mylib"],
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["ok"] is True
            assert data["data"]["submodules"] == ["libs/mylib"]

    def test_create_task_without_submodules(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        (repo / ".claude-flow").mkdir(exist_ok=True)
        app, tm = self._create_app(repo)

        with app.test_client() as client:
            resp = client.post("/api/tasks", json={
                "title": "Test",
                "prompt": "do it",
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["data"]["submodules"] == []

    def test_create_mini_task_with_submodules(self, git_repo_with_submodule):
        info = git_repo_with_submodule
        repo = info["repo"]
        (repo / ".claude-flow").mkdir(exist_ok=True)
        app, tm = self._create_app(repo)

        with app.test_client() as client:
            resp = client.post("/api/mini-tasks", json={
                "title": "Mini Test",
                "prompt": "fix it",
                "submodules": ["libs/mylib"],
            })
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["data"]["submodules"] == ["libs/mylib"]

    def test_get_submodules_list(self, git_repo_with_submodule):
        """GET /api/submodules should return list of submodule paths."""
        info = git_repo_with_submodule
        repo = info["repo"]
        (repo / ".claude-flow").mkdir(exist_ok=True)
        app, tm = self._create_app(repo)

        with app.test_client() as client:
            resp = client.get("/api/submodules")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "libs/mylib" in data["data"]

    def test_get_submodules_non_git(self, non_git_dir):
        """GET /api/submodules in non-git project should return empty list."""
        app, tm = self._create_app(non_git_dir)
        app.config["IS_GIT"] = False

        with app.test_client() as client:
            resp = client.get("/api/submodules")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["data"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_submodule.py::TestWebApiSubmodule -v`
Expected: FAIL — no `/api/submodules` endpoint, `submodules` not passed through

- [ ] **Step 3: Update Web API**

In `claude_flow/web/api.py`, modify `create_task()` to pass submodules:

```python
@api_bp.route("/tasks", methods=["POST"])
def create_task():
    """创建新任务。body: {title, prompt, priority, submodules}"""
    tm = current_app.config["TASK_MANAGER"]
    data = request.get_json(silent=True)

    if not data:
        return _err("请求体不能为空")

    title = data.get("title")
    prompt = data.get("prompt")

    if not title or not prompt:
        return _err("title 和 prompt 为必填字段")

    priority = 0
    raw_priority = data.get("priority")
    if raw_priority is not None:
        try:
            priority = int(raw_priority)
        except (ValueError, TypeError):
            return _err("priority 必须是整数")

    submodules = data.get("submodules", [])
    if not isinstance(submodules, list):
        return _err("submodules 必须是数组")

    task_type = data.get("task_type", "normal")
    if task_type == "mini":
        task = tm.add_mini(title, prompt, priority=priority, submodules=submodules)
    else:
        task = tm.add(title, prompt, priority=priority, submodules=submodules)
    return _ok(task.to_dict()), 201
```

Modify `create_mini_task()` to pass submodules:

```python
@api_bp.route("/mini-tasks", methods=["POST"])
def create_mini_task():
    """Create a mini task. body: {title, prompt, submodules}"""
    tm = current_app.config["TASK_MANAGER"]
    data = request.get_json(silent=True)

    if not data:
        return _err("request body cannot be empty")

    title = data.get("title")
    prompt = data.get("prompt", "")

    if not title:
        return _err("title is required")

    submodules = data.get("submodules", [])
    if not isinstance(submodules, list):
        return _err("submodules must be an array")

    task = tm.add_mini(title, prompt, submodules=submodules)
    return _ok(task.to_dict()), 201
```

Add `GET /api/submodules` endpoint:

```python
@api_bp.route("/submodules", methods=["GET"])
def list_submodules():
    """返回项目中所有 submodule 的路径列表（从 .gitmodules 解析）。"""
    import configparser

    is_git = current_app.config.get("IS_GIT", True)
    if not is_git:
        return _ok([])

    root = current_app.config["PROJECT_ROOT"]
    gitmodules_path = root / ".gitmodules"
    if not gitmodules_path.exists():
        return _ok([])

    parser = configparser.ConfigParser()
    parser.read(str(gitmodules_path))

    paths = []
    for section in parser.sections():
        if parser.has_option(section, "path"):
            paths.append(parser.get(section, "path"))

    return _ok(paths)
```

Modify `start_mini_task()` to pass submodules to worktree create (around line 1029):

```python
        wt_path = wt.create(task_id, branch, config=cfg,
                            submodules=task.submodules or None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_submodule.py::TestWebApiSubmodule -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: No regressions

- [ ] **Step 6: Commit**

```bash
git add claude_flow/web/api.py tests/unit/test_submodule.py
git commit -m "feat(web): submodules support in API + GET /api/submodules endpoint"
```

---

## Chunk 6: Edge Cases & Non-Git Tests

### Task 8: Edge case and error handling tests

**Files:**
- Test: `tests/unit/test_submodule.py`

- [ ] **Step 1: Write edge case tests**

Append to `tests/unit/test_submodule.py`:

```python
class TestSubmoduleEdgeCases:
    def test_non_git_task_add_with_submodule_ignored(self, non_git_dir):
        """In non-git mode, submodules on task should be stored but not acted upon."""
        tm = TaskManager(non_git_dir)
        task = tm.add("Test", "prompt", submodules=["libs/core"])
        assert task.submodules == ["libs/core"]

        # Worker in non-git mode should not crash
        cfg = Config()
        wt = WorktreeManager(non_git_dir, non_git_dir / ".claude-flow" / "worktrees",
                             is_git=False)
        worker = Worker(0, non_git_dir, tm, wt, cfg, is_git=False)
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        # auto_commit in non-git mode: submodule dir won't exist, should skip gracefully
        result = worker._auto_commit(claimed, non_git_dir)
        # No changes made, should return False
        assert result is False

    def test_auto_commit_submodule_dir_missing(self, git_repo_with_submodule):
        """If submodule dir doesn't exist in worktree, skip gracefully."""
        info = git_repo_with_submodule
        repo = info["repo"]
        cf_dir = repo / ".claude-flow"
        cf_dir.mkdir(exist_ok=True)
        (cf_dir / "logs").mkdir(exist_ok=True)
        cfg = Config()
        tm = TaskManager(repo)
        wt_dir = cf_dir / "worktrees"
        wt_dir.mkdir(exist_ok=True)
        wt = WorktreeManager(repo, wt_dir)
        worker = Worker(0, repo, tm, wt, cfg)

        # Create task referencing a submodule that won't be initialized
        task = tm.add("Missing sub", "prompt", submodules=["nonexistent/sub"])
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(0)

        # Create worktree WITHOUT initializing submodule
        wt_path = wt.create(claimed.id, claimed.branch)
        (wt_path / "file.txt").write_text("content")

        # Should not crash, should still commit main project changes
        result = worker._auto_commit(claimed, wt_path)
        assert result is True

    def test_backward_compat_old_tasks_json(self, tmp_path):
        """tasks.json without submodules field should load without error."""
        cf_dir = tmp_path / ".claude-flow"
        cf_dir.mkdir(parents=True)
        tasks_file = cf_dir / "tasks.json"
        # Old format: no submodules key
        old_task = {
            "id": "task-old01",
            "title": "Old Task",
            "prompt": "old prompt",
            "status": "pending",
            "task_type": "normal",
            "branch": None,
            "plan_file": None,
            "worker_id": None,
            "created_at": "2026-01-01T00:00:00",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "priority": 0,
            "progress": None,
            "retry_count": 0,
            "plan_mode": None,
        }
        tasks_file.write_text(json.dumps([old_task]))

        tm = TaskManager(tmp_path)
        tasks = tm.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].submodules == []
        assert tasks[0].title == "Old Task"
```

- [ ] **Step 2: Run all submodule tests**

Run: `pytest tests/unit/test_submodule.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS, no regressions

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_submodule.py
git commit -m "test(submodule): edge cases — non-git, missing dir, backward compat"
```

---

## Final Verification

- [ ] **Run complete test suite**: `pytest tests/ -v --tb=short`
- [ ] **Verify no regressions in existing tests**
- [ ] **Manual smoke test**: In a real repo with submodules, run:
  ```bash
  cf task add -p "modify lib code" -s libs/mylib "Test Submodule"
  cf task list  # should show task
  cf task show <task_id>  # should show Submodules: libs/mylib
  ```
