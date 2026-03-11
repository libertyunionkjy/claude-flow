# Worker Subagent 模式实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让用户可以通过简单开关控制 Worker 执行任务时是否注入 subagent 策略 prompt，全局配置 + 任务级覆盖。

**Architecture:** 在 Config 和 Task 数据模型中各增加一个 `use_subagent` 字段，Worker 构建 prompt 时根据优先级（任务级 > 全局）决定是否追加 subagent 策略常量。CLI 的 `task add` 增加 `--subagent/--no-subagent` 选项。

**Tech Stack:** Python dataclass, Click CLI, pytest

---

### Task 1: Task 模型新增 `use_subagent` 字段

**Files:**
- Modify: `claude_flow/models.py:34-96`
- Test: `tests/unit/test_models.py`

**Step 1: Write the failing test**

在 `tests/unit/test_models.py` 的 `TestTask` 类末尾添加：

```python
def test_use_subagent_default_none(self):
    task = Task(title="Test", prompt="P")
    assert task.use_subagent is None

def test_use_subagent_explicit(self):
    task = Task(title="Test", prompt="P", use_subagent=True)
    assert task.use_subagent is True

def test_use_subagent_roundtrip(self):
    task = Task(title="Test", prompt="P", use_subagent=True)
    d = task.to_dict()
    assert d["use_subagent"] is True
    restored = Task.from_dict(d)
    assert restored.use_subagent is True

def test_use_subagent_missing_in_dict(self):
    """Backward compat: old tasks without use_subagent field."""
    d = {
        "id": "task-old", "title": "Old", "prompt": "P",
        "status": "pending", "created_at": "2026-01-01T00:00:00",
    }
    task = Task.from_dict(d)
    assert task.use_subagent is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_models.py -v -k "subagent"`
Expected: FAIL — `Task.__init__() got an unexpected keyword argument 'use_subagent'`

**Step 3: Write minimal implementation**

在 `claude_flow/models.py` 的 `Task` dataclass 中，`plan_mode` 字段后面添加：

```python
use_subagent: Optional[bool] = None  # None = inherit from config
```

在 `to_dict()` 的返回 dict 中添加：

```python
"use_subagent": self.use_subagent,
```

在 `from_dict()` 的构造参数中添加：

```python
use_subagent=d.get("use_subagent"),
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_models.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add claude_flow/models.py tests/unit/test_models.py
git commit -m "feat(models): add use_subagent field to Task"
```

---

### Task 2: Config 新增 `use_subagent` 字段

**Files:**
- Modify: `claude_flow/config.py:11-46` (DEFAULT_CONFIG) 和 `claude_flow/config.py:50-86` (Config dataclass)
- Test: `tests/unit/test_config.py`

**Step 1: Write the failing test**

在 `tests/unit/test_config.py` 末尾添加测试：

```python
def test_use_subagent_default_false(tmp_path):
    cfg = Config()
    assert cfg.use_subagent is False

def test_use_subagent_from_file(tmp_path):
    cf_dir = tmp_path / ".claude-flow"
    cf_dir.mkdir()
    config_file = cf_dir / "config.json"
    config_file.write_text('{"use_subagent": true}')
    cfg = Config.load(tmp_path)
    assert cfg.use_subagent is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -v -k "subagent"`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'use_subagent'`

**Step 3: Write minimal implementation**

在 `claude_flow/config.py` 的 `DEFAULT_CONFIG` dict 中添加：

```python
# Subagent mode
"use_subagent": False,
```

在 `Config` dataclass 中，`web_port` 字段后添加：

```python
# Subagent mode
use_subagent: bool = False
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add claude_flow/config.py tests/unit/test_config.py
git commit -m "feat(config): add use_subagent option (default false)"
```

---

### Task 3: Worker 提取 `_build_prompt()` 方法并注入 subagent prompt

**Files:**
- Modify: `claude_flow/worker.py:75-142`
- Test: `tests/unit/test_worker.py`

**Step 1: Write the failing tests**

在 `tests/unit/test_worker.py` 的 `TestWorker` 类末尾添加：

```python
def test_build_prompt_without_subagent(self, git_repo):
    """subagent 关闭时，prompt 中不含 subagent 指令。"""
    _, tm, _, worker = self._setup(git_repo)
    task = Task(title="T", prompt="do something")
    result = worker._build_prompt(task)
    assert "do something" in result
    assert "subagent" not in result.lower()
    assert "Task tool" not in result

def test_build_prompt_with_subagent_from_config(self, git_repo):
    """全局 subagent 开启时，prompt 中包含 subagent 指令。"""
    repo, tm, wt, _ = self._setup(git_repo)
    cfg = Config(use_subagent=True)
    worker = Worker(worker_id=0, project_root=repo, task_manager=tm,
                    worktree_manager=wt, config=cfg)
    task = Task(title="T", prompt="do something")
    result = worker._build_prompt(task)
    assert "Task tool" in result

def test_build_prompt_task_overrides_config(self, git_repo):
    """任务级 use_subagent=False 覆盖全局 True。"""
    repo, tm, wt, _ = self._setup(git_repo)
    cfg = Config(use_subagent=True)
    worker = Worker(worker_id=0, project_root=repo, task_manager=tm,
                    worktree_manager=wt, config=cfg)
    task = Task(title="T", prompt="do something", use_subagent=False)
    result = worker._build_prompt(task)
    assert "Task tool" not in result

def test_build_prompt_task_enables_subagent(self, git_repo):
    """任务级 use_subagent=True 覆盖全局 False。"""
    _, tm, _, worker = self._setup(git_repo)
    task = Task(title="T", prompt="do something", use_subagent=True)
    result = worker._build_prompt(task)
    assert "Task tool" in result
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_worker.py -v -k "build_prompt"`
Expected: FAIL — `AttributeError: 'Worker' object has no attribute '_build_prompt'`

**Step 3: Write minimal implementation**

在 `claude_flow/worker.py` 顶部（`logger = ...` 之后）添加常量：

```python
SUBAGENT_PROMPT = (
    "当你面对此任务时，请考虑将其拆分为多个独立子任务并行处理。\n"
    "使用 Task tool 启动 subagent 来并行执行这些子任务。\n"
    "每个 subagent 应该有明确的职责边界，独立完成后汇总结果。\n"
    "优先使用 general-purpose 类型的 subagent。\n"
    "如果子任务之间有依赖关系，按依赖顺序串行执行。\n"
    "如果任务足够简单不需要拆分，直接执行即可。"
)
```

在 `Worker` 类中（`_log_prefix` 之后）添加方法：

```python
def _build_prompt(self, task: Task) -> str:
    """Build the full prompt for a task, optionally injecting subagent instructions."""
    parts = [self._cfg.task_prompt_prefix, task.prompt]

    use_subagent = (
        task.use_subagent
        if task.use_subagent is not None
        else self._cfg.use_subagent
    )
    if use_subagent:
        parts.append(SUBAGENT_PROMPT)

    return "\n\n".join(parts)
```

然后修改 `_execute_task_simple()` 中的 prompt 构建（第 80 行）：

```python
# Before:
prompt = f"{self._cfg.task_prompt_prefix}\n\n{task.prompt}"

# After:
prompt = self._build_prompt(task)
```

修改 `_execute_task_git()` 中的 prompt 构建（第 138 行）：

```python
# Before:
prompt = f"{self._cfg.task_prompt_prefix}\n\n{worktree_constraint}\n\n{task.prompt}"

# After:
base_prompt = self._build_prompt(task)
prompt = f"{base_prompt}\n\n{worktree_constraint}"
```

注意：worktree 约束放在最后，确保其优先级最高。

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_worker.py -v`
Expected: ALL PASS

**Step 5: Run existing test suite to confirm no regression**

Run: `pytest tests/ -v --timeout=60`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add claude_flow/worker.py tests/unit/test_worker.py
git commit -m "feat(worker): extract _build_prompt with subagent injection"
```

---

### Task 4: CLI `task add` 新增 `--subagent` 选项

**Files:**
- Modify: `claude_flow/cli.py:117-137` (`task_add` command)
- Modify: `claude_flow/task_manager.py` (`add()` method — 传递 `use_subagent` 参数)
- Test: `tests/unit/test_models.py` (已在 Task 1 覆盖)

**Step 1: 检查 TaskManager.add() 签名**

Read `claude_flow/task_manager.py` 的 `add()` 方法，确认如何传递新参数。

**Step 2: Write the failing test**

在 `tests/e2e/test_e2e_cli.py` 或新建 `tests/unit/test_cli_subagent.py` 中添加：

```python
import re
from pathlib import Path
from click.testing import CliRunner
from claude_flow.cli import main
from claude_flow.task_manager import TaskManager


def test_task_add_with_subagent_flag(tmp_path: Path):
    """cf task add --subagent should set use_subagent=True on the task."""
    runner = CliRunner()
    env = {"CF_PROJECT_ROOT": str(tmp_path)}
    # init
    (tmp_path / ".claude-flow").mkdir()
    runner.invoke(main, ["init"], env=env)

    result = runner.invoke(
        main,
        ["task", "add", "Test", "-p", "do it", "--subagent"],
        env=env,
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    match = re.search(r"(task-[a-f0-9]+)", result.output)
    assert match
    task_id = match.group(1)

    tm = TaskManager(tmp_path)
    task = tm.get(task_id)
    assert task.use_subagent is True


def test_task_add_with_no_subagent_flag(tmp_path: Path):
    """cf task add --no-subagent should set use_subagent=False."""
    runner = CliRunner()
    env = {"CF_PROJECT_ROOT": str(tmp_path)}
    (tmp_path / ".claude-flow").mkdir()
    runner.invoke(main, ["init"], env=env)

    result = runner.invoke(
        main,
        ["task", "add", "Test", "-p", "do it", "--no-subagent"],
        env=env,
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    match = re.search(r"(task-[a-f0-9]+)", result.output)
    task_id = match.group(1)

    tm = TaskManager(tmp_path)
    task = tm.get(task_id)
    assert task.use_subagent is False


def test_task_add_without_subagent_flag(tmp_path: Path):
    """Without flag, use_subagent should be None (inherit from config)."""
    runner = CliRunner()
    env = {"CF_PROJECT_ROOT": str(tmp_path)}
    (tmp_path / ".claude-flow").mkdir()
    runner.invoke(main, ["init"], env=env)

    result = runner.invoke(
        main,
        ["task", "add", "Test", "-p", "do it"],
        env=env,
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    match = re.search(r"(task-[a-f0-9]+)", result.output)
    task_id = match.group(1)

    tm = TaskManager(tmp_path)
    task = tm.get(task_id)
    assert task.use_subagent is None
```

**Step 3: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_subagent.py -v`
Expected: FAIL — click option `--subagent` not recognized

**Step 4: Write minimal implementation**

在 `claude_flow/cli.py` 的 `task_add` 命令中添加 Click 选项：

```python
@task.command("add")
@click.argument("title")
@click.option("-p", "--prompt", default=None, help="Task prompt for Claude Code")
@click.option("-f", "--file", "filepath", default=None, type=click.Path(exists=True), help="Import tasks from file")
@click.option("-P", "--priority", default=0, type=int, help="Task priority (higher = more important)")
@click.option("--subagent/--no-subagent", default=None, help="Enable subagent mode for this task")
@click.pass_context
def task_add(ctx, title, prompt, filepath, priority, subagent):
```

在 `tm.add()` 调用处传递 `use_subagent=subagent`：

```python
t = tm.add(title, prompt, priority=priority, use_subagent=subagent)
```

在 `claude_flow/task_manager.py` 的 `add()` 方法中增加 `use_subagent` 参数：

```python
def add(self, title: str, prompt: str, priority: int = 0, use_subagent: Optional[bool] = None) -> Task:
    task = Task(title=title, prompt=prompt, priority=priority, use_subagent=use_subagent)
    ...
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_cli_subagent.py -v`
Expected: ALL PASS

**Step 6: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add claude_flow/cli.py claude_flow/task_manager.py tests/unit/test_cli_subagent.py
git commit -m "feat(cli): add --subagent/--no-subagent option to task add"
```

---

### Task 5: `task list` 输出中显示 subagent 标记

**Files:**
- Modify: `claude_flow/cli.py:182-200` (`task_list` command)

**Step 1: Write the failing test**

在 `tests/unit/test_cli_subagent.py` 中添加：

```python
def test_task_list_shows_subagent_marker(tmp_path: Path):
    """task list should show [S] for subagent-enabled tasks."""
    runner = CliRunner()
    env = {"CF_PROJECT_ROOT": str(tmp_path)}
    (tmp_path / ".claude-flow").mkdir()
    runner.invoke(main, ["init"], env=env)
    runner.invoke(main, ["task", "add", "WithSA", "-p", "prompt", "--subagent"], env=env)
    runner.invoke(main, ["task", "add", "Without", "-p", "prompt"], env=env)

    result = runner.invoke(main, ["task", "list"], env=env, catch_exceptions=False)
    assert result.exit_code == 0
    # Find the line for WithSA and check it has [S]
    for line in result.output.splitlines():
        if "WithSA" in line:
            assert "[S]" in line
        if "Without" in line:
            assert "[S]" not in line
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_subagent.py::test_task_list_shows_subagent_marker -v`
Expected: FAIL — `[S]` not in output

**Step 3: Write minimal implementation**

修改 `claude_flow/cli.py` 的 `task_list` 命令中的格式化行（约第 200 行）：

```python
# Before:
tag = "[mini] " if t.is_mini else ""

# After:
tag = "[mini] " if t.is_mini else ""
if t.use_subagent:
    tag += "[S] "
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_cli_subagent.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add claude_flow/cli.py tests/unit/test_cli_subagent.py
git commit -m "feat(cli): show [S] marker for subagent tasks in list"
```

---

### Task 6: 全量回归测试 + 最终验证

**Files:** None (testing only)

**Step 1: Run the full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: ALL PASS, no regressions

**Step 2: Manual verification**

Run these commands in a temporary project to verify the feature works:

```bash
cd /tmp && mkdir subagent-test && cd subagent-test && git init -b main
cf init
cf task add "Simple task" -p "print hello"
cf task add "Complex task" -p "refactor everything" --subagent
cf task list
# Expected: Complex task has [S] marker, Simple task does not
cf task show <complex-task-id>
```

**Step 3: Verify backward compatibility**

Manually create a `tasks.json` without `use_subagent` field and ensure `cf task list` works without errors.

**Step 4: Commit (if any cleanup needed)**

No commit expected unless issues found.
