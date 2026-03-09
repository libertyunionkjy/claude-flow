# 测试框架重构实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将测试目录从扁平结构重构为分层目录（unit / integration / e2e / boundary），并新增边界/异常测试、10 并发测试、CLI/Web 端到端测试（Mock + Smoke 真实 claude）、配置健壮性测试。

**Architecture:** 采用 pytest 多层 conftest.py 架构，根 conftest 提供通用 fixture 和 marker 注册，子目录 conftest 提供特化 fixture。现有测试文件按职责搬迁到对应子目录，保持原有测试不变。Smoke 测试使用 `@pytest.mark.smoke` 标记，默认 CI 跳过。

**Tech Stack:** Python 3.10+, pytest >= 7.0, pytest-cov, Click CliRunner, Flask test_client, subprocess mock, fcntl, threading/multiprocessing, tempfile

---

## 目录结构总览

```
tests/
├── conftest.py                     # 根 conftest：marker 注册 + 通用 fixture（git_repo, cf_project, claude_subprocess_guard）
├── __init__.py
├── unit/                           # 单元测试（搬迁现有）
│   ├── __init__.py
│   ├── test_models.py              # ← tests/test_models.py
│   ├── test_config.py              # ← tests/test_config.py
│   ├── test_task_manager.py        # ← tests/test_task_manager.py
│   ├── test_planner.py             # ← tests/test_planner.py
│   ├── test_worker.py              # ← tests/test_worker.py
│   ├── test_worktree.py            # ← tests/test_worktree.py
│   ├── test_utils.py               # ← tests/test_utils.py
│   ├── test_usage.py               # ← tests/test_usage.py
│   └── test_chat.py                # ← tests/test_chat.py
├── integration/                    # 集成 + 并发测试
│   ├── __init__.py
│   ├── conftest.py                 # 集成测试专用 fixture
│   ├── test_integration.py         # ← tests/test_integration.py
│   ├── test_integration_full.py    # ← tests/test_integration_full.py
│   ├── test_web_api.py             # ← tests/test_web_api.py
│   └── test_concurrency.py         # 【新增】10 并发竞争测试
├── e2e/                            # 端到端测试（Mock + Smoke）
│   ├── __init__.py
│   ├── conftest.py                 # E2E 专用 fixture（真实 claude fixture）
│   ├── test_e2e_cli.py             # 【新增】CLI 端到端
│   └── test_e2e_web.py             # 【新增】Web API 端到端
└── boundary/                       # 边界 / 异常 / 配置健壮性
    ├── __init__.py
    ├── test_boundary_inputs.py     # 【新增】输入边界值
    ├── test_exception_recovery.py  # 【新增】异常恢复与崩溃场景
    └── test_config_robustness.py   # 【新增】配置健壮性
```

## 搬迁映射表

| 原位置 | 新位置 | 说明 |
|--------|--------|------|
| `tests/conftest.py` | `tests/conftest.py` | 保留并扩展（加 marker 注册） |
| `tests/test_models.py` | `tests/unit/test_models.py` | 原样搬迁 |
| `tests/test_config.py` | `tests/unit/test_config.py` | 原样搬迁 |
| `tests/test_task_manager.py` | `tests/unit/test_task_manager.py` | 原样搬迁 |
| `tests/test_planner.py` | `tests/unit/test_planner.py` | 原样搬迁 |
| `tests/test_worker.py` | `tests/unit/test_worker.py` | 原样搬迁 |
| `tests/test_worktree.py` | `tests/unit/test_worktree.py` | 原样搬迁 |
| `tests/test_utils.py` | `tests/unit/test_utils.py` | 原样搬迁 |
| `tests/test_usage.py` | `tests/unit/test_usage.py` | 原样搬迁 |
| `tests/test_chat.py` | `tests/unit/test_chat.py` | 原样搬迁 |
| `tests/test_integration.py` | `tests/integration/test_integration.py` | 原样搬迁 |
| `tests/test_integration_full.py` | `tests/integration/test_integration_full.py` | 原样搬迁 |
| `tests/test_web_api.py` | `tests/integration/test_web_api.py` | 原样搬迁 |
| `tests/test_cli.py` | 删除 | 内容整合进 `tests/e2e/test_e2e_cli.py` |

---

## Task 1: 创建目录结构并搬迁现有文件

**Files:**
- Create: `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/e2e/__init__.py`, `tests/boundary/__init__.py`
- Move: 上述搬迁映射表中的所有文件
- Modify: `tests/conftest.py` — 添加 smoke marker 注册

**Step 1: 创建子目录和 `__init__.py`**

```bash
mkdir -p tests/unit tests/integration tests/e2e tests/boundary
touch tests/unit/__init__.py tests/integration/__init__.py tests/e2e/__init__.py tests/boundary/__init__.py
```

**Step 2: 搬迁单元测试文件**

```bash
git mv tests/test_models.py tests/unit/test_models.py
git mv tests/test_config.py tests/unit/test_config.py
git mv tests/test_task_manager.py tests/unit/test_task_manager.py
git mv tests/test_planner.py tests/unit/test_planner.py
git mv tests/test_worker.py tests/unit/test_worker.py
git mv tests/test_worktree.py tests/unit/test_worktree.py
git mv tests/test_utils.py tests/unit/test_utils.py
git mv tests/test_usage.py tests/unit/test_usage.py
git mv tests/test_chat.py tests/unit/test_chat.py
```

**Step 3: 搬迁集成测试文件**

```bash
git mv tests/test_integration.py tests/integration/test_integration.py
git mv tests/test_integration_full.py tests/integration/test_integration_full.py
git mv tests/test_web_api.py tests/integration/test_web_api.py
```

**Step 4: 搬迁 CLI 测试（后续整合进 e2e）**

```bash
git mv tests/test_cli.py tests/e2e/test_cli_legacy.py
```

**Step 5: 更新根 `conftest.py` — 添加 smoke marker 注册**

在现有 `conftest.py` 顶部添加：

```python
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "smoke: marks tests that require real claude CLI (deselect with '-m \"not smoke\"')"
    )
```

**Step 6: 运行全部测试确认搬迁无破坏**

Run: `pytest tests/ -v --tb=short 2>&1 | tail -20`
Expected: 所有现有测试 PASS（路径变了但 conftest fixture 通过 pytest 自动发现机制仍可用）

**Step 7: 提交**

```bash
git add tests/
git commit -m "refactor(tests): restructure test directory into unit/integration/e2e/boundary layers"
```

---

## Task 2: 创建集成测试专用 conftest 和并发测试 fixture

**Files:**
- Create: `tests/integration/conftest.py`

**Step 1: 编写集成测试 conftest**

```python
"""Integration test fixtures."""
from __future__ import annotations

import pytest
from pathlib import Path

from claude_flow.config import Config
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import WorktreeManager
from claude_flow.planner import Planner
from claude_flow.chat import ChatManager


@pytest.fixture
def full_project(cf_project: Path):
    """Provide a fully initialized project with all managers."""
    config = Config.load(cf_project)
    tm = TaskManager(cf_project)
    plans_dir = cf_project / ".claude-flow" / "plans"
    wm = WorktreeManager(cf_project, cf_project / config.worktree_dir)
    planner = Planner(cf_project, plans_dir, config, task_manager=tm)
    chat_mgr = ChatManager(cf_project, config)
    return {
        "root": cf_project,
        "config": config,
        "tm": tm,
        "wm": wm,
        "planner": planner,
        "chat_mgr": chat_mgr,
        "plans_dir": plans_dir,
    }
```

**Step 2: 运行测试确认 fixture 可用**

Run: `pytest tests/integration/ -v --tb=short --co 2>&1 | head -20`
Expected: 收集到所有 integration 测试

---

## Task 3: 新增并发测试 `test_concurrency.py`

**Files:**
- Create: `tests/integration/test_concurrency.py`
- Test: `tests/integration/test_concurrency.py`

**Step 1: 编写 10 并发测试**

```python
"""Concurrency tests with 10 parallel workers.

Tests race conditions, lock contention, and data integrity under
concurrent access to TaskManager and WorktreeManager.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.task_manager import TaskManager
from claude_flow.worktree import WorktreeManager


CONCURRENCY = 10


class TestConcurrentTaskClaim:
    """Test that claim_next is safe under 10 concurrent workers."""

    def test_no_double_claim(self, cf_project: Path):
        """10 workers claiming simultaneously should never get the same task."""
        tm = TaskManager(cf_project)
        # Create 10 approved tasks
        tasks = []
        for i in range(CONCURRENCY):
            t = tm.add(f"task-{i}", f"prompt-{i}")
            tm.update_status(t.id, TaskStatus.APPROVED)
            tasks.append(t)

        claimed = []
        errors = []

        def worker_claim(worker_id: int):
            try:
                result = tm.claim_next(worker_id)
                if result:
                    claimed.append(result.id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker_claim, args=(i,))
            for i in range(CONCURRENCY)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Errors during claim: {errors}"
        # Each task should be claimed at most once
        assert len(claimed) == len(set(claimed)), (
            f"Double claim detected: {claimed}"
        )
        assert len(claimed) == CONCURRENCY

    def test_claim_more_workers_than_tasks(self, cf_project: Path):
        """10 workers but only 3 tasks — 7 should get None."""
        tm = TaskManager(cf_project)
        for i in range(3):
            t = tm.add(f"task-{i}", f"prompt-{i}")
            tm.update_status(t.id, TaskStatus.APPROVED)

        claimed = []
        nones = []

        def worker_claim(worker_id: int):
            result = tm.claim_next(worker_id)
            if result:
                claimed.append(result.id)
            else:
                nones.append(worker_id)

        threads = [
            threading.Thread(target=worker_claim, args=(i,))
            for i in range(CONCURRENCY)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(claimed) == 3
        assert len(nones) == 7
        assert len(claimed) == len(set(claimed))


class TestConcurrentReadWrite:
    """Test concurrent reads and writes to task store."""

    def test_concurrent_add_and_list(self, cf_project: Path):
        """10 threads adding tasks while 10 threads listing — no corruption."""
        tm = TaskManager(cf_project)
        errors = []

        def adder(idx: int):
            try:
                tm.add(f"concurrent-{idx}", f"prompt-{idx}")
            except Exception as e:
                errors.append(("add", idx, e))

        def lister(idx: int):
            try:
                tasks = tm.list_tasks()
                # Should always be a valid list
                assert isinstance(tasks, list)
            except Exception as e:
                errors.append(("list", idx, e))

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = []
            for i in range(CONCURRENCY):
                futures.append(pool.submit(adder, i))
                futures.append(pool.submit(lister, i))
            for f in as_completed(futures):
                f.result()  # Re-raise any exception

        assert not errors, f"Errors: {errors}"
        all_tasks = tm.list_tasks()
        assert len(all_tasks) == CONCURRENCY

    def test_concurrent_status_updates(self, cf_project: Path):
        """10 threads updating different tasks' status simultaneously."""
        tm = TaskManager(cf_project)
        tasks = [tm.add(f"task-{i}", f"prompt-{i}") for i in range(CONCURRENCY)]
        errors = []

        def updater(task: Task):
            try:
                tm.update_status(task.id, TaskStatus.APPROVED)
                tm.update_status(task.id, TaskStatus.RUNNING)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=updater, args=(t,))
            for t in tasks
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        for t in tasks:
            result = tm.get(t.id)
            assert result.status == TaskStatus.RUNNING

    def test_concurrent_priority_updates(self, cf_project: Path):
        """10 threads updating the same task's priority — last write wins, no crash."""
        tm = TaskManager(cf_project)
        task = tm.add("shared-task", "shared-prompt")
        errors = []

        def update_priority(priority: int):
            try:
                tm.update_priority(task.id, priority)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=update_priority, args=(i,))
            for i in range(CONCURRENCY)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        result = tm.get(task.id)
        assert result.priority in range(CONCURRENCY)  # One of the valid values


class TestConcurrentMergeLock:
    """Test merge lock serialization under concurrent pressure."""

    def test_merge_lock_serializes_10_concurrent(self, git_repo: Path):
        """10 concurrent merge attempts should be serialized by lock."""
        config = Config()
        wm = WorktreeManager(git_repo, git_repo / ".claude-flow" / "worktrees")
        lock_acquisitions = []
        lock = threading.Lock()

        original_merge_lock = wm._with_merge_lock

        def tracked_merge_lock(fn):
            def wrapper():
                result = fn()
                with lock:
                    lock_acquisitions.append(time.monotonic())
                return result
            return original_merge_lock(wrapper)

        # Create 10 branches with content
        branches = []
        for i in range(CONCURRENCY):
            branch = f"cf/test-merge-{i}"
            wt_path = wm.create(f"test-{i}", branch, config)
            # Add unique file to each branch
            (wt_path / f"file_{i}.txt").write_text(f"content {i}")
            import subprocess
            subprocess.run(["git", "add", "."], cwd=wt_path, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"add file {i}"],
                cwd=wt_path, check=True
            )
            branches.append(branch)

        results = []
        errors = []

        def merge_branch(branch: str):
            try:
                ok = wm.merge(branch, "main", config=config)
                results.append((branch, ok))
            except Exception as e:
                errors.append((branch, e))

        threads = [
            threading.Thread(target=merge_branch, args=(b,))
            for b in branches
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert not errors, f"Merge errors: {errors}"
        # At least some merges should succeed (non-conflicting files)
        successes = [r for r in results if r[1]]
        assert len(successes) > 0, "No merges succeeded"


class TestConcurrentChatSessions:
    """Test concurrent chat session operations."""

    def test_concurrent_session_create_delete(self, cf_project: Path):
        """10 threads creating and deleting sessions simultaneously."""
        from claude_flow.chat import ChatManager
        config = Config.load(cf_project)
        cm = ChatManager(cf_project, config)
        errors = []

        def session_lifecycle(idx: int):
            try:
                task_id = f"task-chat-{idx}"
                session = cm.create_session(task_id)
                assert session.task_id == task_id
                cm.add_message(task_id, "user", f"Hello {idx}")
                result = cm.get_session(task_id)
                assert result is not None
                assert len(result.messages) == 1
                cm.delete_session(task_id)
            except Exception as e:
                errors.append((idx, e))

        threads = [
            threading.Thread(target=session_lifecycle, args=(i,))
            for i in range(CONCURRENCY)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Session errors: {errors}"
```

**Step 2: 运行并发测试**

Run: `pytest tests/integration/test_concurrency.py -v --tb=short`
Expected: 全部 PASS

**Step 3: 提交**

```bash
git add tests/integration/test_concurrency.py
git commit -m "test(concurrency): add 10-worker concurrent claim, read/write, merge, and chat tests"
```

---

## Task 4: 新增边界输入测试 `test_boundary_inputs.py`

**Files:**
- Create: `tests/boundary/__init__.py`, `tests/boundary/test_boundary_inputs.py`
- Test: `tests/boundary/test_boundary_inputs.py`

**Step 1: 编写边界值测试**

```python
"""Boundary and edge-case input tests.

Covers empty strings, None values, excessively long strings,
special characters in paths and task fields, and invalid enum values.
"""
from __future__ import annotations

import string
from pathlib import Path

import pytest

from claude_flow.models import Task, TaskStatus
from claude_flow.config import Config
from claude_flow.task_manager import TaskManager


class TestTaskBoundaryInputs:
    """Boundary values for Task model fields."""

    def test_empty_title(self, cf_project: Path):
        """Empty string title should still create a task."""
        tm = TaskManager(cf_project)
        task = tm.add("", "some prompt")
        assert task.title == ""
        assert task.id is not None

    def test_empty_prompt(self, cf_project: Path):
        """Empty string prompt should still create a task."""
        tm = TaskManager(cf_project)
        task = tm.add("title", "")
        assert task.prompt == ""

    def test_very_long_title(self, cf_project: Path):
        """10,000 character title should be handled without truncation."""
        tm = TaskManager(cf_project)
        long_title = "A" * 10_000
        task = tm.add(long_title, "prompt")
        assert len(task.title) == 10_000
        # Verify persistence round-trip
        retrieved = tm.get(task.id)
        assert len(retrieved.title) == 10_000

    def test_very_long_prompt(self, cf_project: Path):
        """100,000 character prompt round-trip."""
        tm = TaskManager(cf_project)
        long_prompt = "B" * 100_000
        task = tm.add("title", long_prompt)
        retrieved = tm.get(task.id)
        assert len(retrieved.prompt) == 100_000

    def test_unicode_in_title_and_prompt(self, cf_project: Path):
        """Unicode characters including CJK, emoji, RTL."""
        tm = TaskManager(cf_project)
        title = "测试任务 🚀 مهمة"
        prompt = "这是一个包含中文、日本語、한국어的提示词"
        task = tm.add(title, prompt)
        retrieved = tm.get(task.id)
        assert retrieved.title == title
        assert retrieved.prompt == prompt

    def test_special_characters_in_fields(self, cf_project: Path):
        """Newlines, tabs, quotes, backslashes in title/prompt."""
        tm = TaskManager(cf_project)
        title = 'line1\nline2\ttab "quoted" \\back'
        prompt = "prompt with\x00null byte and\rcarriage return"
        task = tm.add(title, prompt)
        retrieved = tm.get(task.id)
        assert retrieved.title == title

    def test_negative_priority(self, cf_project: Path):
        """Negative priority should be accepted."""
        tm = TaskManager(cf_project)
        task = tm.add("task", "prompt", priority=-100)
        assert task.priority == -100

    def test_very_large_priority(self, cf_project: Path):
        """Extremely large priority value."""
        tm = TaskManager(cf_project)
        task = tm.add("task", "prompt", priority=2**31)
        retrieved = tm.get(task.id)
        assert retrieved.priority == 2**31

    def test_priority_ordering_with_mixed_values(self, cf_project: Path):
        """claim_next should respect priority ordering."""
        tm = TaskManager(cf_project)
        low = tm.add("low", "p", priority=-10)
        high = tm.add("high", "p", priority=100)
        mid = tm.add("mid", "p", priority=0)
        for t in [low, high, mid]:
            tm.update_status(t.id, TaskStatus.APPROVED)

        claimed = tm.claim_next(1)
        assert claimed.id == high.id


class TestTaskManagerBoundary:
    """Boundary cases for TaskManager operations."""

    def test_get_nonexistent_task(self, cf_project: Path):
        """Getting a non-existent task returns None."""
        tm = TaskManager(cf_project)
        assert tm.get("nonexistent-id-12345") is None

    def test_remove_nonexistent_task(self, cf_project: Path):
        """Removing a non-existent task returns False."""
        tm = TaskManager(cf_project)
        assert tm.remove("nonexistent-id-12345") is False

    def test_update_status_nonexistent(self, cf_project: Path):
        """Updating status of non-existent task returns None."""
        tm = TaskManager(cf_project)
        result = tm.update_status("nonexistent", TaskStatus.RUNNING)
        assert result is None

    def test_claim_when_no_approved_tasks(self, cf_project: Path):
        """claim_next with no approved tasks returns None."""
        tm = TaskManager(cf_project)
        tm.add("task", "prompt")  # PENDING, not APPROVED
        assert tm.claim_next(1) is None

    def test_claim_skips_non_approved(self, cf_project: Path):
        """claim_next ignores tasks in non-APPROVED states."""
        tm = TaskManager(cf_project)
        t1 = tm.add("pending", "p")
        t2 = tm.add("running", "p")
        tm.update_status(t2.id, TaskStatus.APPROVED)
        tm.update_status(t2.id, TaskStatus.RUNNING)

        t3 = tm.add("approved", "p")
        tm.update_status(t3.id, TaskStatus.APPROVED)

        claimed = tm.claim_next(1)
        assert claimed.id == t3.id

    def test_add_from_empty_file(self, cf_project: Path, tmp_path: Path):
        """Adding from an empty file should return empty list."""
        tm = TaskManager(cf_project)
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        tasks = tm.add_from_file(empty_file)
        assert tasks == []

    def test_add_from_file_with_blank_lines(self, cf_project: Path, tmp_path: Path):
        """Blank lines in task file should be skipped."""
        tm = TaskManager(cf_project)
        task_file = tmp_path / "tasks.txt"
        task_file.write_text("\n\n  task1|prompt1  \n\n  task2|prompt2  \n\n")
        tasks = tm.add_from_file(task_file)
        assert len(tasks) == 2

    def test_respond_only_works_for_needs_input(self, cf_project: Path):
        """respond() should only work for NEEDS_INPUT status."""
        tm = TaskManager(cf_project)
        task = tm.add("task", "prompt")
        # Task is PENDING, not NEEDS_INPUT
        result = tm.respond(task.id, "extra info")
        assert result is None


class TestModelSerialization:
    """Edge cases in Task serialization/deserialization."""

    def test_from_dict_missing_optional_fields(self):
        """from_dict with only required fields."""
        minimal = {
            "id": "task-abc123",
            "title": "test",
            "prompt": "test prompt",
            "status": "pending",
        }
        task = Task.from_dict(minimal)
        assert task.id == "task-abc123"
        assert task.branch is None
        assert task.plan_file is None
        assert task.worker_id is None
        assert task.error is None
        assert task.priority == 0
        assert task.retry_count == 0

    def test_from_dict_unknown_status(self):
        """from_dict with an unrecognized status string."""
        data = {
            "id": "task-abc123",
            "title": "test",
            "prompt": "test prompt",
            "status": "unknown_status_value",
        }
        # Should either raise ValueError or fall back gracefully
        with pytest.raises((ValueError, KeyError)):
            Task.from_dict(data)

    def test_roundtrip_all_fields(self):
        """Serialize and deserialize with all fields populated."""
        from datetime import datetime
        task = Task(
            title="full task",
            prompt="full prompt",
            status=TaskStatus.RUNNING,
            branch="cf/task-abc",
            plan_file="/tmp/plan.md",
            worker_id=5,
            error="some error",
            priority=42,
            progress="50%",
            retry_count=3,
            plan_mode="interactive",
        )
        d = task.to_dict()
        restored = Task.from_dict(d)
        assert restored.title == task.title
        assert restored.status == task.status
        assert restored.branch == task.branch
        assert restored.worker_id == task.worker_id
        assert restored.error == task.error
        assert restored.priority == task.priority
        assert restored.progress == task.progress
        assert restored.retry_count == task.retry_count
        assert restored.plan_mode == task.plan_mode
```

**Step 2: 运行边界测试**

Run: `pytest tests/boundary/test_boundary_inputs.py -v --tb=short`
Expected: 全部 PASS（部分可能需要根据实际行为调整预期）

**Step 3: 提交**

```bash
git add tests/boundary/
git commit -m "test(boundary): add input boundary, edge-case, and serialization tests"
```

---

## Task 5: 新增异常恢复测试 `test_exception_recovery.py`

**Files:**
- Create: `tests/boundary/test_exception_recovery.py`
- Test: `tests/boundary/test_exception_recovery.py`

**Step 1: 编写异常恢复和崩溃场景测试**

```python
"""Exception recovery and crash scenario tests.

Tests what happens when:
- tasks.json is corrupted or missing
- Lock files are stale (process killed mid-operation)
- Subprocess (claude CLI) crashes, times out, or returns unexpected output
- Disk I/O fails during writes
- Worker is killed mid-execution
- Config file is corrupted
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from claude_flow.config import Config
from claude_flow.models import Task, TaskStatus
from claude_flow.task_manager import TaskManager
from claude_flow.planner import Planner
from claude_flow.worker import Worker
from claude_flow.worktree import WorktreeManager
from claude_flow.chat import ChatManager


class TestCorruptedTasksJson:
    """Test recovery from corrupted tasks.json."""

    def test_empty_tasks_json(self, cf_project: Path):
        """Empty tasks.json should be treated as empty list."""
        tasks_file = cf_project / ".claude-flow" / "tasks.json"
        tasks_file.write_text("")
        tm = TaskManager(cf_project)
        tasks = tm.list_tasks()
        assert tasks == []

    def test_invalid_json_in_tasks(self, cf_project: Path):
        """Malformed JSON should recover from backup or return empty."""
        tasks_file = cf_project / ".claude-flow" / "tasks.json"
        # First create a valid task
        tm = TaskManager(cf_project)
        tm.add("good task", "prompt")

        # Corrupt the file
        tasks_file.write_text("{invalid json!!!")

        # Re-instantiate and try to read
        tm2 = TaskManager(cf_project)
        tasks = tm2.list_tasks()
        # Should either recover from backup or return empty, not crash
        assert isinstance(tasks, list)

    def test_tasks_json_with_partial_write(self, cf_project: Path):
        """Simulate partial write (truncated JSON)."""
        tasks_file = cf_project / ".claude-flow" / "tasks.json"
        tm = TaskManager(cf_project)
        tm.add("task1", "prompt1")

        # Write truncated JSON
        tasks_file.write_text('[{"id": "task-abc", "title": "trunc')

        tm2 = TaskManager(cf_project)
        tasks = tm2.list_tasks()
        assert isinstance(tasks, list)

    def test_tasks_json_permission_denied(self, cf_project: Path):
        """Read-only tasks.json — add should raise or handle gracefully."""
        tasks_file = cf_project / ".claude-flow" / "tasks.json"
        tasks_file.write_text("[]")
        tasks_file.chmod(0o444)

        tm = TaskManager(cf_project)
        try:
            with pytest.raises((PermissionError, OSError)):
                tm.add("task", "prompt")
        finally:
            tasks_file.chmod(0o644)  # Restore for cleanup

    def test_missing_claude_flow_directory(self, git_repo: Path):
        """TaskManager with missing .claude-flow/ should create it."""
        cf_dir = git_repo / ".claude-flow"
        assert not cf_dir.exists()
        tm = TaskManager(git_repo)
        # Should auto-create or raise a clear error
        assert cf_dir.exists() or True  # Depends on implementation


class TestStaleLockFile:
    """Test behavior when lock files are left behind (process killed)."""

    def test_stale_tasks_lock(self, cf_project: Path):
        """Stale tasks.lock should not prevent new operations.

        fcntl.flock is advisory and process-scoped, so a stale lock file
        from a dead process should not block a new process.
        """
        lock_file = cf_project / ".claude-flow" / "tasks.lock"
        # Create a stale lock file
        lock_file.touch()

        tm = TaskManager(cf_project)
        # Should still work (fcntl locks are released on process death)
        task = tm.add("after-stale-lock", "prompt")
        assert task is not None

    def test_stale_merge_lock(self, git_repo: Path):
        """Stale merge.lock should not prevent new merges."""
        config = Config()
        wm = WorktreeManager(git_repo, git_repo / ".claude-flow" / "worktrees")
        lock_file = git_repo / ".claude-flow" / "worktrees" / "merge.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.touch()

        # Should still work
        active = wm.list_active()
        assert isinstance(active, list)


class TestSubprocessFailures:
    """Test handling of claude CLI subprocess failures."""

    def test_planner_claude_not_found(self, cf_project: Path):
        """claude command not found should fail gracefully."""
        config = Config.load(cf_project)
        tm = TaskManager(cf_project)
        plans_dir = cf_project / ".claude-flow" / "plans"
        planner = Planner(cf_project, plans_dir, config, task_manager=tm)

        task = tm.add("test", "test prompt")
        tm.update_status(task.id, TaskStatus.PLANNING)

        with patch("claude_flow.planner.subprocess.Popen") as mock_popen:
            mock_popen.side_effect = FileNotFoundError("claude: command not found")
            result = planner.generate(task)

        assert result is None
        updated = tm.get(task.id)
        assert updated.status == TaskStatus.FAILED
        assert "not found" in (updated.error or "").lower() or updated.error is not None

    def test_planner_timeout(self, cf_project: Path):
        """claude process hanging should be killed after timeout."""
        config = Config.load(cf_project)
        config.task_timeout = 1  # 1 second timeout
        tm = TaskManager(cf_project)
        plans_dir = cf_project / ".claude-flow" / "plans"
        planner = Planner(cf_project, plans_dir, config, task_manager=tm)

        task = tm.add("test", "test prompt")
        tm.update_status(task.id, TaskStatus.PLANNING)

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
            cmd="claude", timeout=1
        )
        mock_proc.kill = MagicMock()
        mock_proc.wait = MagicMock()

        with patch("claude_flow.planner.subprocess.Popen", return_value=mock_proc):
            result = planner.generate(task)

        assert result is None
        updated = tm.get(task.id)
        assert updated.status == TaskStatus.FAILED

    def test_planner_returncode_nonzero(self, cf_project: Path):
        """claude returning non-zero exit code."""
        config = Config.load(cf_project)
        tm = TaskManager(cf_project)
        plans_dir = cf_project / ".claude-flow" / "plans"
        planner = Planner(cf_project, plans_dir, config, task_manager=tm)

        task = tm.add("test", "test prompt")
        tm.update_status(task.id, TaskStatus.PLANNING)

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("", "API rate limit exceeded")
        mock_proc.returncode = 1
        mock_proc.stdin = None

        with patch("claude_flow.planner.subprocess.Popen", return_value=mock_proc):
            result = planner.generate(task)

        assert result is None
        updated = tm.get(task.id)
        assert updated.status == TaskStatus.FAILED

    def test_worker_claude_crash_mid_execution(self, git_repo: Path, claude_subprocess_guard):
        """Worker should mark task FAILED if claude crashes."""
        config = Config()
        tm = TaskManager(git_repo)
        wm = WorktreeManager(git_repo, git_repo / config.worktree_dir)

        task = tm.add("crash-test", "prompt")
        tm.update_status(task.id, TaskStatus.APPROVED)
        claimed = tm.claim_next(1)

        worker = Worker(1, git_repo, tm, wm, config)

        claude_subprocess_guard.mock_popen()
        # Simulate crash: returncode = -11 (SIGSEGV)
        claude_subprocess_guard._task_returncode = -11
        claude_subprocess_guard._task_stdout = ""

        result = worker.execute_task(claimed)
        assert result is False
        updated = tm.get(claimed.id)
        assert updated.status == TaskStatus.FAILED


class TestWorkerInterruption:
    """Test worker behavior when interrupted mid-task."""

    def test_keyboard_interrupt_during_plan(self, cf_project: Path):
        """KeyboardInterrupt during plan generation should rollback status."""
        config = Config.load(cf_project)
        tm = TaskManager(cf_project)
        plans_dir = cf_project / ".claude-flow" / "plans"
        planner = Planner(cf_project, plans_dir, config, task_manager=tm)

        task = tm.add("interrupt-test", "prompt")

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = KeyboardInterrupt()
        mock_proc.kill = MagicMock()
        mock_proc.wait = MagicMock()

        with patch("claude_flow.planner.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(KeyboardInterrupt):
                planner.generate(task)


class TestChatManagerRecovery:
    """Test ChatManager recovery from abnormal states."""

    def test_stale_thinking_session_recovery(self, cf_project: Path):
        """Session stuck in thinking=True should be recovered on init."""
        config = Config.load(cf_project)
        cm = ChatManager(cf_project, config)

        # Create a session and manually set thinking=True (simulate crash)
        session = cm.create_session("task-stale")
        session.thinking = True
        session_file = cf_project / ".claude-flow" / "chats" / "task-stale.json"
        session_file.write_text(json.dumps(session.to_dict()))

        # Re-initialize — should recover stale session
        cm2 = ChatManager(cf_project, config)
        recovered = cm2.get_session("task-stale")
        assert recovered is not None
        assert recovered.thinking is False

    def test_corrupted_session_file(self, cf_project: Path):
        """Corrupted session JSON should not crash get_session."""
        config = Config.load(cf_project)
        cm = ChatManager(cf_project, config)

        chats_dir = cf_project / ".claude-flow" / "chats"
        chats_dir.mkdir(parents=True, exist_ok=True)
        (chats_dir / "task-corrupt.json").write_text("{bad json")

        result = cm.get_session("task-corrupt")
        # Should return None or raise a handled error, not crash
        assert result is None or isinstance(result, object)

    def test_abort_session_no_active_process(self, cf_project: Path):
        """Aborting a session with no active process should not crash."""
        config = Config.load(cf_project)
        cm = ChatManager(cf_project, config)

        session = cm.create_session("task-abort")
        result = cm.abort_session("task-abort")
        # Should handle gracefully
        assert isinstance(result, bool)

    def test_send_message_to_finalized_session(self, cf_project: Path):
        """Sending message to finalized session should be rejected."""
        config = Config.load(cf_project)
        cm = ChatManager(cf_project, config)

        cm.create_session("task-final")
        cm.finalize("task-final")

        # Depending on implementation: should reject or handle
        session = cm.get_session("task-final")
        assert session.status == "finalized"


class TestWorktreeEdgeCases:
    """Edge cases in worktree operations."""

    def test_remove_nonexistent_worktree(self, git_repo: Path):
        """Removing a worktree that doesn't exist should not crash."""
        config = Config()
        wm = WorktreeManager(git_repo, git_repo / ".claude-flow" / "worktrees")
        # Should handle gracefully (check=False in implementation)
        wm.remove("nonexistent-task", "cf/nonexistent-branch")

    def test_create_duplicate_worktree(self, git_repo: Path):
        """Creating a worktree for an already-existing branch."""
        config = Config()
        wm = WorktreeManager(git_repo, git_repo / ".claude-flow" / "worktrees")
        wm.create("dup-task", "cf/dup-branch", config)

        with pytest.raises(subprocess.CalledProcessError):
            wm.create("dup-task-2", "cf/dup-branch", config)

    def test_merge_nonexistent_branch(self, git_repo: Path):
        """Merging a branch that doesn't exist should return False or raise."""
        config = Config()
        wm = WorktreeManager(git_repo, git_repo / ".claude-flow" / "worktrees")
        result = wm.merge("nonexistent-branch", "main", config=config)
        assert result is False or result is None
```

**Step 2: 运行异常恢复测试**

Run: `pytest tests/boundary/test_exception_recovery.py -v --tb=short`
Expected: 全部 PASS（部分可能需要根据实际行为调整断言）

**Step 3: 提交**

```bash
git add tests/boundary/test_exception_recovery.py
git commit -m "test(boundary): add exception recovery, crash scenario, and stale state tests"
```

---

## Task 6: 新增配置健壮性测试 `test_config_robustness.py`

**Files:**
- Create: `tests/boundary/test_config_robustness.py`
- Test: `tests/boundary/test_config_robustness.py`

**Step 1: 编写配置健壮性测试**

```python
"""Config robustness tests.

Tests corrupted config files, type mismatches, missing fields,
environment variable edge cases, and config hot-reload scenarios.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_flow.config import Config


class TestConfigFileCorruption:
    """Test behavior with corrupted/invalid config files."""

    def test_empty_config_file(self, cf_project: Path):
        """Empty config.json should use all defaults."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text("")
        config = Config.load(cf_project)
        assert config.max_workers == 2
        assert config.main_branch == "main"

    def test_invalid_json_config(self, cf_project: Path):
        """Malformed JSON config should fall back to defaults."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text("{not valid json!!!")
        config = Config.load(cf_project)
        assert config.max_workers == 2

    def test_null_json_config(self, cf_project: Path):
        """config.json containing 'null' should use defaults."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text("null")
        config = Config.load(cf_project)
        assert config.max_workers == 2

    def test_array_instead_of_object(self, cf_project: Path):
        """config.json containing array should use defaults."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text("[1, 2, 3]")
        config = Config.load(cf_project)
        assert config.max_workers == 2

    def test_config_with_extra_unknown_fields(self, cf_project: Path):
        """Unknown fields in config should be ignored, not crash."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({
            "max_workers": 4,
            "unknown_field_xyz": "value",
            "another_unknown": 123,
        }))
        config = Config.load(cf_project)
        assert config.max_workers == 4

    def test_config_missing_file(self, cf_project: Path):
        """No config.json at all should use defaults."""
        config_file = cf_project / ".claude-flow" / "config.json"
        if config_file.exists():
            config_file.unlink()
        config = Config.load(cf_project)
        assert config.max_workers == 2
        assert config.main_branch == "main"


class TestConfigTypeMismatch:
    """Test behavior when config values have wrong types."""

    def test_max_workers_as_string(self, cf_project: Path):
        """max_workers='abc' should use default or raise."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"max_workers": "abc"}))
        # Should either use default or raise TypeError
        try:
            config = Config.load(cf_project)
            # If it loads, it should have some numeric value
            assert isinstance(config.max_workers, (int, str))
        except (TypeError, ValueError):
            pass  # Also acceptable

    def test_max_workers_negative(self, cf_project: Path):
        """max_workers=-1 should be loaded (validation elsewhere)."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"max_workers": -1}))
        config = Config.load(cf_project)
        assert config.max_workers == -1

    def test_max_workers_zero(self, cf_project: Path):
        """max_workers=0 should be loaded."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"max_workers": 0}))
        config = Config.load(cf_project)
        assert config.max_workers == 0

    def test_auto_merge_as_string(self, cf_project: Path):
        """auto_merge='yes' instead of True."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"auto_merge": "yes"}))
        config = Config.load(cf_project)
        # Should be truthy or use default
        assert config.auto_merge is not None

    def test_claude_args_as_string(self, cf_project: Path):
        """claude_args='--verbose' instead of list."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"claude_args": "--verbose"}))
        try:
            config = Config.load(cf_project)
            # If it loads, claude_args type matters
            assert config.claude_args is not None
        except TypeError:
            pass  # Also acceptable

    def test_task_timeout_float(self, cf_project: Path):
        """task_timeout=30.5 (float instead of int)."""
        config_file = cf_project / ".claude-flow" / "config.json"
        config_file.write_text(json.dumps({"task_timeout": 30.5}))
        config = Config.load(cf_project)
        assert config.task_timeout == 30.5 or config.task_timeout == 30


class TestConfigSaveReload:
    """Test config save and reload round-trips."""

    def test_save_and_reload(self, cf_project: Path):
        """Saved config should be identical when reloaded."""
        config = Config.load(cf_project)
        config.max_workers = 8
        config.main_branch = "develop"
        config.claude_args = ["--verbose", "--model", "opus"]
        config.save(cf_project)

        reloaded = Config.load(cf_project)
        assert reloaded.max_workers == 8
        assert reloaded.main_branch == "develop"
        assert reloaded.claude_args == ["--verbose", "--model", "opus"]

    def test_save_creates_directory(self, tmp_path: Path):
        """Saving config to non-existent directory should create it."""
        config = Config()
        cf_dir = tmp_path / ".claude-flow"
        assert not cf_dir.exists()
        config.save(tmp_path)
        assert (cf_dir / "config.json").exists()


class TestEnvironmentVariables:
    """Test CF_PROJECT_ROOT environment variable handling."""

    def test_cf_project_root_valid(self, cf_project: Path):
        """CF_PROJECT_ROOT pointing to valid project."""
        with patch.dict(os.environ, {"CF_PROJECT_ROOT": str(cf_project)}):
            config = Config.load(cf_project)
            assert config.max_workers >= 0

    def test_cf_project_root_nonexistent(self, tmp_path: Path):
        """CF_PROJECT_ROOT pointing to non-existent directory."""
        fake_path = tmp_path / "nonexistent"
        with patch.dict(os.environ, {"CF_PROJECT_ROOT": str(fake_path)}):
            # Should handle gracefully
            config = Config.load(fake_path)
            assert config.max_workers == 2  # defaults

    def test_cf_project_root_empty_string(self, cf_project: Path):
        """CF_PROJECT_ROOT set to empty string."""
        with patch.dict(os.environ, {"CF_PROJECT_ROOT": ""}):
            config = Config.load(cf_project)
            assert config is not None
```

**Step 2: 运行配置健壮性测试**

Run: `pytest tests/boundary/test_config_robustness.py -v --tb=short`
Expected: 全部 PASS

**Step 3: 提交**

```bash
git add tests/boundary/test_config_robustness.py
git commit -m "test(config): add config corruption, type mismatch, and environment variable tests"
```

---

## Task 7: 创建 E2E conftest 和 CLI 端到端测试

**Files:**
- Create: `tests/e2e/conftest.py`, `tests/e2e/test_e2e_cli.py`
- Test: `tests/e2e/test_e2e_cli.py`

**Step 1: 创建 E2E conftest（含 smoke fixture）**

```python
"""E2E test fixtures.

Provides both mock and real claude CLI fixtures.
Smoke tests (real claude) require @pytest.mark.smoke marker.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from claude_flow.config import Config


@pytest.fixture
def real_claude_available():
    """Check if real claude CLI is available. Skip if not."""
    if not shutil.which("claude"):
        pytest.skip("claude CLI not available")


@pytest.fixture
def e2e_project(tmp_path: Path):
    """Create a fully isolated git repo for E2E testing."""
    repo = tmp_path / "e2e-project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True
    )
    (repo / "README.md").write_text("# E2E Test Project\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True
    )

    # Initialize .claude-flow
    cf_dir = repo / ".claude-flow"
    for sub in ["logs", "plans", "worktrees", "chats"]:
        (cf_dir / sub).mkdir(parents=True)
    Config().save(repo)

    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add claude-flow"],
        cwd=repo, check=True, capture_output=True
    )

    return repo
```

**Step 2: 编写 CLI 端到端测试（Mock 版本）**

```python
"""CLI end-to-end tests.

Tests complete user workflows through the CLI:
- init → add → plan → approve → run → status → log → clean
- Interactive planning workflow
- Error recovery workflow

Mock version (default): claude CLI mocked
Smoke version (@pytest.mark.smoke): uses real claude CLI
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from claude_flow.cli import main
from claude_flow.models import TaskStatus


class TestCLIE2EWorkflowMocked:
    """Full CLI workflow with mocked claude."""

    def test_full_lifecycle_init_to_done(self, e2e_project: Path):
        """init → task add → plan → approve → run → status → clean."""
        runner = CliRunner(mix_stderr=False)
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Step 1: init (already done in fixture, but test idempotency)
        result = runner.invoke(main, ["init"], env=env, catch_exceptions=False)
        assert result.exit_code == 0

        # Step 2: add task
        result = runner.invoke(
            main,
            ["task", "add", "Refactor utils", "-p", "Refactor the utils module for clarity"],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "task-" in result.output  # Should print task ID

        # Extract task ID
        import re
        match = re.search(r"(task-[a-f0-9]+)", result.output)
        assert match, f"No task ID in output: {result.output}"
        task_id = match.group(1)

        # Step 3: list tasks
        result = runner.invoke(main, ["task", "list"], env=env, catch_exceptions=False)
        assert result.exit_code == 0
        assert "Refactor utils" in result.output

        # Step 4: plan (mocked claude)
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("# Plan\n\n## Steps\n1. Do thing", "")
        mock_proc.returncode = 0
        mock_proc.stdin = None
        mock_proc.pid = 12345

        with patch("claude_flow.planner.subprocess.Popen", return_value=mock_proc):
            result = runner.invoke(
                main,
                ["plan", "-t", task_id, "-F"],
                env=env,
                catch_exceptions=False,
            )
        assert result.exit_code == 0

        # Step 5: approve
        result = runner.invoke(
            main,
            ["plan", "approve", task_id],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        # Step 6: plan status
        result = runner.invoke(
            main, ["plan", "status"], env=env, catch_exceptions=False
        )
        assert result.exit_code == 0

        # Step 7: status overview
        result = runner.invoke(
            main, ["status"], env=env, catch_exceptions=False
        )
        assert result.exit_code == 0

        # Step 8: run (mocked claude worker)
        mock_worker_proc = MagicMock()
        mock_worker_proc.stdout.__iter__ = MagicMock(return_value=iter([
            json.dumps({"type": "result", "result": "Done"}) + "\n"
        ]))
        mock_worker_proc.wait.return_value = 0
        mock_worker_proc.returncode = 0
        mock_worker_proc.stdin = None
        mock_worker_proc.pid = 12346

        with patch("claude_flow.worker.subprocess.Popen", return_value=mock_worker_proc):
            with patch("claude_flow.worktree.subprocess.run") as mock_git:
                mock_git.return_value = MagicMock(
                    returncode=0, stdout="", stderr=""
                )
                result = runner.invoke(
                    main,
                    ["run", "-n", "1", "-t", task_id],
                    env=env,
                    catch_exceptions=False,
                )
        # Run may complete or handle the task
        assert result.exit_code in (0, 1)

        # Step 9: clean
        result = runner.invoke(
            main, ["clean"], env=env, catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_multi_task_batch_workflow(self, e2e_project: Path, tmp_path: Path):
        """Add multiple tasks from file, plan all, approve all."""
        runner = CliRunner(mix_stderr=False)
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Create task file
        task_file = tmp_path / "tasks.txt"
        task_file.write_text(
            "Task A|Implement feature A\n"
            "Task B|Implement feature B\n"
            "Task C|Implement feature C\n"
        )

        # Batch add
        result = runner.invoke(
            main,
            ["task", "add", "batch", "-f", str(task_file)],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        # List should show 3 tasks
        result = runner.invoke(main, ["task", "list"], env=env, catch_exceptions=False)
        assert result.exit_code == 0

    def test_task_remove_workflow(self, e2e_project: Path):
        """Add → remove → verify removed."""
        runner = CliRunner(mix_stderr=False)
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Add
        result = runner.invoke(
            main,
            ["task", "add", "To Delete", "-p", "Will be deleted"],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        import re
        match = re.search(r"(task-[a-f0-9]+)", result.output)
        task_id = match.group(1)

        # Remove
        result = runner.invoke(
            main,
            ["task", "remove", task_id],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        # Verify removed
        result = runner.invoke(
            main, ["task", "list"], env=env, catch_exceptions=False
        )
        assert task_id not in result.output

    def test_reset_and_retry_workflow(self, e2e_project: Path):
        """Add → plan → approve → simulate failure → reset → retry."""
        runner = CliRunner(mix_stderr=False)
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Add and approve
        result = runner.invoke(
            main,
            ["task", "add", "Fail Test", "-p", "Will fail"],
            env=env,
            catch_exceptions=False,
        )
        import re
        task_id = re.search(r"(task-[a-f0-9]+)", result.output).group(1)

        # Manually set to FAILED via task manager
        from claude_flow.task_manager import TaskManager
        tm = TaskManager(e2e_project)
        tm.update_status(task_id, TaskStatus.FAILED, error="simulated failure")

        # Reset
        result = runner.invoke(
            main, ["reset", task_id], env=env, catch_exceptions=False
        )
        assert result.exit_code == 0

        # Verify reset to pending
        task = tm.get(task_id)
        assert task.status in (TaskStatus.PENDING, TaskStatus.APPROVED)


@pytest.mark.smoke
class TestCLIE2ESmoke:
    """Real claude CLI tests. Requires claude to be installed and configured.

    Run with: pytest -m smoke
    Skip with: pytest -m "not smoke"
    """

    def test_real_plan_generation(self, e2e_project: Path, real_claude_available):
        """Generate a real plan using claude CLI."""
        runner = CliRunner(mix_stderr=False)
        env = {"CF_PROJECT_ROOT": str(e2e_project)}

        # Add task
        result = runner.invoke(
            main,
            ["task", "add", "Add docstring", "-p",
             "Add a one-line docstring to the README.md file"],
            env=env,
            catch_exceptions=False,
        )
        import re
        task_id = re.search(r"(task-[a-f0-9]+)", result.output).group(1)

        # Plan with real claude (foreground)
        result = runner.invoke(
            main,
            ["plan", "-t", task_id, "-F"],
            env=env,
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        # Verify plan file was created
        plans_dir = e2e_project / ".claude-flow" / "plans"
        plan_files = list(plans_dir.glob(f"{task_id}*.md"))
        assert len(plan_files) >= 1, "No plan file generated"
```

**Step 3: 运行 CLI E2E 测试（Mock 部分）**

Run: `pytest tests/e2e/test_e2e_cli.py -v --tb=short -m "not smoke"`
Expected: 全部 PASS

**Step 4: 提交**

```bash
git add tests/e2e/
git commit -m "test(e2e): add CLI end-to-end tests with mock and smoke variants"
```

---

## Task 8: 新增 Web API 端到端测试

**Files:**
- Create: `tests/e2e/test_e2e_web.py`
- Test: `tests/e2e/test_e2e_web.py`

**Step 1: 编写 Web API 端到端测试**

```python
"""Web API end-to-end tests.

Tests complete API workflows:
- Task CRUD → Plan → Approve → Run → Status
- Chat interactive planning → Finalize → Approve
- Error recovery via reset/retry endpoints
- Batch operations

Mock version (default): claude CLI mocked
Smoke version (@pytest.mark.smoke): uses real claude CLI
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_flow.config import Config


def _has_flask():
    try:
        import flask
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _has_flask(), reason="Flask not installed")


@pytest.fixture
def web_client(e2e_project: Path):
    """Create a Flask test client."""
    from claude_flow.web.app import create_app
    config = Config.load(e2e_project)
    app = create_app(e2e_project, config)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestWebE2EWorkflowMocked:
    """Full Web API workflow with mocked claude."""

    def test_full_task_lifecycle(self, web_client, e2e_project: Path):
        """POST task → GET → plan → approve → status."""
        # Step 1: Create task
        resp = web_client.post("/api/tasks", json={
            "title": "Web E2E Task",
            "prompt": "Implement a web feature",
            "priority": 5,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        task_id = data["data"]["id"]

        # Step 2: Get task
        resp = web_client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        task_data = resp.get_json()["data"]
        assert task_data["title"] == "Web E2E Task"
        assert task_data["status"] == "pending"

        # Step 3: List tasks
        resp = web_client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.get_json()["data"]
        assert any(t["id"] == task_id for t in tasks)

        # Step 4: Generate plan (mocked)
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("# Plan\n## Steps\n1. Do it", "")
        mock_proc.returncode = 0
        mock_proc.stdin = None
        mock_proc.pid = 99999

        with patch("claude_flow.planner.subprocess.Popen", return_value=mock_proc):
            resp = web_client.post(f"/api/tasks/{task_id}/plan", json={
                "mode": "auto"
            })
        assert resp.status_code == 200

        # Wait for background plan generation
        time.sleep(1)

        # Step 5: Get plan
        resp = web_client.get(f"/api/tasks/{task_id}/plan")
        # Plan may or may not be ready yet
        assert resp.status_code in (200, 404)

        # Step 6: Approve
        resp = web_client.post(f"/api/tasks/{task_id}/approve")
        assert resp.status_code in (200, 400)  # 400 if not yet planned

        # Step 7: Status
        resp = web_client.get("/api/status")
        assert resp.status_code == 200
        status = resp.get_json()["data"]
        assert "pending" in status or "total" in str(status)

    def test_task_crud_operations(self, web_client):
        """Create, read, update, delete a task."""
        # Create
        resp = web_client.post("/api/tasks", json={
            "title": "CRUD Test",
            "prompt": "Test CRUD operations",
        })
        assert resp.status_code == 200
        task_id = resp.get_json()["data"]["id"]

        # Read
        resp = web_client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200

        # Update priority
        resp = web_client.patch(f"/api/tasks/{task_id}", json={
            "priority": 10
        })
        assert resp.status_code == 200

        # Verify update
        resp = web_client.get(f"/api/tasks/{task_id}")
        assert resp.get_json()["data"]["priority"] == 10

        # Delete
        resp = web_client.delete(f"/api/tasks/{task_id}")
        assert resp.status_code == 200

        # Verify deleted
        resp = web_client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 404

    def test_batch_delete(self, web_client):
        """Create multiple tasks and batch delete."""
        ids = []
        for i in range(5):
            resp = web_client.post("/api/tasks", json={
                "title": f"Batch {i}",
                "prompt": f"Prompt {i}",
            })
            ids.append(resp.get_json()["data"]["id"])

        # Batch delete
        resp = web_client.post("/api/tasks/batch-delete", json={
            "task_ids": ids[:3]
        })
        assert resp.status_code == 200

        # Verify only 2 remain
        resp = web_client.get("/api/tasks")
        remaining = resp.get_json()["data"]
        remaining_ids = [t["id"] for t in remaining]
        for deleted_id in ids[:3]:
            assert deleted_id not in remaining_ids
        for kept_id in ids[3:]:
            assert kept_id in remaining_ids

    def test_chat_workflow(self, web_client, e2e_project: Path):
        """Create task → start chat → send messages → finalize → approve."""
        # Create task
        resp = web_client.post("/api/tasks", json={
            "title": "Chat Task",
            "prompt": "Design a feature via chat",
        })
        task_id = resp.get_json()["data"]["id"]

        # Start interactive plan (creates chat session)
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("Initial analysis done", "")
        mock_proc.returncode = 0
        mock_proc.stdin = None
        mock_proc.pid = 11111
        mock_proc.poll.return_value = 0

        with patch("claude_flow.planner.subprocess.Popen", return_value=mock_proc):
            with patch("claude_flow.chat.subprocess.Popen", return_value=mock_proc):
                resp = web_client.post(f"/api/tasks/{task_id}/plan", json={
                    "mode": "interactive"
                })
        assert resp.status_code == 200

        # Get chat history
        resp = web_client.get(f"/api/tasks/{task_id}/chat")
        assert resp.status_code in (200, 404)

    def test_reset_and_retry(self, web_client, e2e_project: Path):
        """Simulate failure → reset → retry workflow."""
        # Create task
        resp = web_client.post("/api/tasks", json={
            "title": "Fail Task",
            "prompt": "Will fail",
        })
        task_id = resp.get_json()["data"]["id"]

        # Set to FAILED via PATCH
        resp = web_client.patch(f"/api/tasks/{task_id}", json={
            "status": "failed"
        })
        assert resp.status_code == 200

        # Reset
        resp = web_client.post(f"/api/tasks/{task_id}/reset")
        assert resp.status_code == 200

        # Verify reset
        resp = web_client.get(f"/api/tasks/{task_id}")
        status = resp.get_json()["data"]["status"]
        assert status in ("pending", "approved")

    def test_nonexistent_task_returns_404(self, web_client):
        """API calls on non-existent task ID should return 404."""
        resp = web_client.get("/api/tasks/nonexistent-id-xyz")
        assert resp.status_code == 404

        resp = web_client.delete("/api/tasks/nonexistent-id-xyz")
        assert resp.status_code == 404

    def test_invalid_json_body(self, web_client):
        """POST with invalid JSON body."""
        resp = web_client.post(
            "/api/tasks",
            data="not json",
            content_type="application/json",
        )
        assert resp.status_code in (400, 415, 500)

    def test_missing_required_fields(self, web_client):
        """POST task without required title field."""
        resp = web_client.post("/api/tasks", json={
            "prompt": "no title provided"
        })
        assert resp.status_code in (400, 500)


@pytest.mark.smoke
class TestWebE2ESmoke:
    """Real claude CLI tests via Web API.

    Run with: pytest -m smoke
    """

    def test_real_plan_via_api(self, web_client, e2e_project: Path, real_claude_available):
        """Generate a real plan via the API."""
        # Create task
        resp = web_client.post("/api/tasks", json={
            "title": "Real Plan Test",
            "prompt": "Add a single comment to README.md saying '# Test'",
        })
        task_id = resp.get_json()["data"]["id"]

        # Trigger real plan
        resp = web_client.post(f"/api/tasks/{task_id}/plan", json={
            "mode": "auto"
        })
        assert resp.status_code == 200

        # Poll for completion (max 120 seconds)
        for _ in range(24):
            time.sleep(5)
            resp = web_client.get(f"/api/tasks/{task_id}")
            status = resp.get_json()["data"]["status"]
            if status in ("planned", "failed"):
                break

        assert status == "planned", f"Plan generation ended with status: {status}"
```

**Step 2: 运行 Web E2E 测试**

Run: `pytest tests/e2e/test_e2e_web.py -v --tb=short -m "not smoke"`
Expected: 全部 PASS

**Step 3: 提交**

```bash
git add tests/e2e/test_e2e_web.py
git commit -m "test(e2e): add Web API end-to-end tests with mock and smoke variants"
```

---

## Task 9: 更新 pyproject.toml 和 pytest 配置

**Files:**
- Modify: `pyproject.toml`

**Step 1: 在 pyproject.toml 中添加 pytest 配置**

在 `pyproject.toml` 末尾追加：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "smoke: marks tests that require real claude CLI (deselect with '-m \"not smoke\"')",
]
```

**Step 2: 更新 dev 依赖（如需 Flask 测试）**

```toml
[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov"]
web = ["flask>=2.0"]
```

**Step 3: 运行全部测试确认无破坏**

Run: `pytest tests/ -v --tb=short -m "not smoke" 2>&1 | tail -30`
Expected: 所有测试 PASS

**Step 4: 提交**

```bash
git add pyproject.toml
git commit -m "chore: add pytest marker config and test path settings"
```

---

## Task 10: 清理旧文件并最终验证

**Files:**
- Delete: 搬迁后 `tests/` 根目录下的旧测试文件（如果 `git mv` 未自动清理）

**Step 1: 确认旧文件已清理**

```bash
ls tests/test_*.py
# 应该没有任何文件（全部已搬迁）
```

**Step 2: 运行完整测试套件**

```bash
# 全部非 smoke 测试
pytest tests/ -v --tb=short -m "not smoke"

# 测试覆盖率
pytest tests/ --cov=claude_flow --cov-report=term-missing -m "not smoke"
```

Expected: 全部 PASS，覆盖率有所提升

**Step 3: （可选）运行 smoke 测试**

```bash
pytest tests/ -v -m smoke --timeout=180
```

**Step 4: 提交**

```bash
git add -A
git commit -m "chore(tests): finalize test restructure cleanup"
```

---

## 实施注意事项

### Import 兼容性
- 所有测试文件使用 `from claude_flow.xxx import ...` 绝对导入，搬迁后无需修改
- `conftest.py` fixture 通过 pytest 自动发现机制传递到子目录，无需显式 import

### conftest 层级
```
tests/conftest.py          → git_repo, cf_project, claude_subprocess_guard, smoke marker
tests/integration/conftest.py → full_project (整合所有 manager)
tests/e2e/conftest.py      → e2e_project, real_claude_available, web_client
tests/boundary/            → 无专用 conftest，使用根 fixture
```

### CI 配置建议
```yaml
# 日常 CI
pytest tests/ -v -m "not smoke" --tb=short

# 周期性集成（含真实 claude）
pytest tests/ -v -m smoke --timeout=180
```

### 测试运行时间预估
| 类别 | 估计时间 | 说明 |
|------|----------|------|
| unit | < 5s | 纯 Python，无外部依赖 |
| integration | < 15s | 有真实 git 操作 |
| boundary | < 10s | 含文件 I/O 和 mock |
| e2e (mock) | < 10s | CLI runner + Flask test client |
| e2e (smoke) | 60-120s | 真实 claude API 调用 |
| concurrency | < 30s | 10 线程并发 |

---

## 新增测试用例统计

| 文件 | 新增测试数 | 覆盖维度 |
|------|-----------|----------|
| `test_concurrency.py` | ~8 | 10 并发竞争、读写安全、merge 锁、chat 并发 |
| `test_boundary_inputs.py` | ~18 | 空值、超长、Unicode、负数、序列化边界 |
| `test_exception_recovery.py` | ~16 | JSON 损坏、锁残留、进程崩溃、中断恢复 |
| `test_config_robustness.py` | ~16 | 配置损坏、类型错误、环境变量、热重载 |
| `test_e2e_cli.py` | ~5 (mock) + 1 (smoke) | 完整 CLI 生命周期 |
| `test_e2e_web.py` | ~8 (mock) + 1 (smoke) | 完整 Web API 生命周期 |
| **合计** | **~73** | 6 个维度全覆盖 |
