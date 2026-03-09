# Merge Lock Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a cross-process file lock around the merge operation in WorktreeManager, preventing race conditions when multiple Workers merge to main simultaneously.

**Architecture:** Add `_with_merge_lock` to `WorktreeManager` using `fcntl.flock` (same pattern as `TaskManager._with_lock`). Wrap the existing `merge` and `rebase_and_merge` methods' core logic inside this lock. No changes needed in `worker.py`.

**Tech Stack:** Python `fcntl.flock`, existing `WorktreeManager` class

**Design doc:** `docs/plans/2026-03-09-merge-lock-design.md`

---

### Task 1: Add `_with_merge_lock` method and test

**Files:**
- Modify: `claude_flow/worktree.py:1-13` (add import)
- Modify: `claude_flow/worktree.py:19-22` (add lock path init)
- Modify: `claude_flow/worktree.py` (add `_with_merge_lock` method)
- Test: `tests/test_worktree.py`

**Step 1: Write the failing test**

Add to `tests/test_worktree.py`:

```python
def test_merge_lock_exists(self, git_repo):
    """Verify _with_merge_lock creates lock file and executes fn."""
    wt_dir = git_repo / ".claude-flow" / "worktrees"
    mgr = WorktreeManager(git_repo, wt_dir)
    result = mgr._with_merge_lock(lambda: 42)
    assert result == 42
    assert (git_repo / ".claude-flow" / "merge.lock").exists()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_worktree.py::TestWorktreeManager::test_merge_lock_exists -v`
Expected: FAIL with "AttributeError: 'WorktreeManager' object has no attribute '_with_merge_lock'"

**Step 3: Write minimal implementation**

In `claude_flow/worktree.py`:

1. Add `import fcntl` at the top (line 3).

2. In `__init__`, add merge lock path (after line 22):

```python
self._merge_lock_file = self._repo / ".claude-flow" / "merge.lock"
```

3. Add new method after `_run` (after line 36):

```python
def _with_merge_lock(self, fn):
    """Execute fn with exclusive merge file lock.

    Ensures only one Worker can merge to main at a time.
    Lock is automatically released when process exits or crashes.
    """
    self._merge_lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(self._merge_lock_file, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_worktree.py::TestWorktreeManager::test_merge_lock_exists -v`
Expected: PASS

**Step 5: Commit**

```bash
git add claude_flow/worktree.py tests/test_worktree.py
git commit -m "feat: add _with_merge_lock to WorktreeManager"
```

---

### Task 2: Wrap `merge` method with lock

**Files:**
- Modify: `claude_flow/worktree.py:109-117` (`merge` method)
- Test: `tests/test_worktree.py`

**Step 1: Write the failing test**

Add to `tests/test_worktree.py`:

```python
def test_merge_acquires_lock(self, git_repo):
    """Verify merge() calls _with_merge_lock internally."""
    from unittest.mock import patch
    wt_dir = git_repo / ".claude-flow" / "worktrees"
    mgr = WorktreeManager(git_repo, wt_dir)
    wt_path = mgr.create("task-lock1", "cf/task-lock1")
    (wt_path / "lock_test.txt").write_text("hello")
    subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "add file"], check=True, capture_output=True)
    with patch.object(mgr, '_with_merge_lock', wraps=mgr._with_merge_lock) as mock_lock:
        success = mgr.merge("cf/task-lock1", "main")
        assert success is True
        assert mock_lock.called
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_worktree.py::TestWorktreeManager::test_merge_acquires_lock -v`
Expected: FAIL at `assert mock_lock.called` (merge doesn't use lock yet)

**Step 3: Modify `merge` method**

Replace the `merge` method in `claude_flow/worktree.py`:

```python
def merge(self, branch: str, main_branch: str, strategy: str = "--no-ff") -> bool:
    def _do_merge():
        try:
            self._run(["git", "checkout", main_branch])
            self._run(["git", "merge", strategy, branch, "-m", f"merge {branch}"])
            return True
        except subprocess.CalledProcessError:
            self._run(["git", "merge", "--abort"], check=False)
            self._run(["git", "checkout", main_branch], check=False)
            return False
    return self._with_merge_lock(_do_merge)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_worktree.py::TestWorktreeManager::test_merge_acquires_lock -v`
Expected: PASS

Also run existing merge tests to ensure no regression:

Run: `pytest tests/test_worktree.py::TestWorktreeManager::test_merge_to_main tests/test_worktree.py::TestWorktreeManager::test_merge_conflict_returns_false -v`
Expected: PASS

**Step 5: Commit**

```bash
git add claude_flow/worktree.py tests/test_worktree.py
git commit -m "feat: wrap merge() with merge lock"
```

---

### Task 3: Wrap `rebase_and_merge` method with lock

**Files:**
- Modify: `claude_flow/worktree.py:128-197` (`rebase_and_merge` method)
- Test: `tests/test_worktree.py`

**Step 1: Write the failing test**

Add to `tests/test_worktree.py`:

```python
def test_rebase_and_merge_acquires_lock(self, git_repo):
    """Verify rebase_and_merge() calls _with_merge_lock internally."""
    from unittest.mock import patch
    wt_dir = git_repo / ".claude-flow" / "worktrees"
    mgr = WorktreeManager(git_repo, wt_dir)
    wt_path = mgr.create("task-lock2", "cf/task-lock2")
    (wt_path / "rebase_test.txt").write_text("hello")
    subprocess.run(["git", "-C", str(wt_path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(wt_path), "commit", "-m", "add file"], check=True, capture_output=True)
    with patch.object(mgr, '_with_merge_lock', wraps=mgr._with_merge_lock) as mock_lock:
        success = mgr.rebase_and_merge("cf/task-lock2", "main")
        assert success is True
        assert mock_lock.called
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_worktree.py::TestWorktreeManager::test_rebase_and_merge_acquires_lock -v`
Expected: FAIL at `assert mock_lock.called`

**Step 3: Modify `rebase_and_merge` method**

Wrap the entire body of `rebase_and_merge` inside `_with_merge_lock`. The method signature stays the same, but internally delegates to a closure:

```python
def rebase_and_merge(self, branch: str, main_branch: str, max_retries: int = 5,
                     config: Config = None) -> bool:
    def _do_rebase_and_merge():
        has_remote = self._has_remote()
        rebase_target = f"origin/{main_branch}" if has_remote else main_branch
        wt_path = self._find_worktree_path(branch)

        # Step 1: fetch
        if has_remote:
            self._run(["git", "fetch", "origin"], check=False, timeout=NETWORK_TIMEOUT)

        # Step 2: rebase in worktree
        rebase_result = self._run(
            ["git", "rebase", rebase_target],
            cwd=wt_path, check=False,
        )

        if rebase_result.returncode == 0:
            return self._ff_merge(branch, main_branch)

        # Step 4: conflict resolution with retries
        skip_ok = config is not None and can_skip_permissions(
            getattr(config, "skip_permissions", False)
        )

        for _ in range(max_retries):
            if not skip_ok:
                break

            claude_cmd = ["claude", "-p", "resolve rebase conflict", "--dangerously-skip-permissions"]
            claude_result = self._run(
                claude_cmd,
                cwd=wt_path, check=False,
            )

            if claude_result.returncode != 0:
                break

            self._run(["git", "add", "-A"], cwd=wt_path, check=False)

            continue_result = self._run(
                ["git", "rebase", "--continue"],
                cwd=wt_path, check=False,
            )

            if continue_result.returncode == 0:
                return self._ff_merge(branch, main_branch)

        # All retries failed
        self._run(["git", "rebase", "--abort"], cwd=wt_path, check=False)
        return False

    return self._with_merge_lock(_do_rebase_and_merge)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_worktree.py -v`
Expected: ALL PASS (including existing tests)

**Step 5: Commit**

```bash
git add claude_flow/worktree.py tests/test_worktree.py
git commit -m "feat: wrap rebase_and_merge() with merge lock"
```

---

### Task 4: Full regression test

**Files:**
- No file changes, verification only

**Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: ALL PASS

**Step 2: Verify worker tests still pass**

Run: `pytest tests/test_worker.py -v`
Expected: ALL PASS (worker.py not changed, but verify mock interactions are intact)

**Step 3: Commit (if any fix needed)**

Only commit if a regression fix was required.
