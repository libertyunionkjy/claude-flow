# Integration Tests (Full Coverage) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** 创建综合集成测试文件 `tests/test_integration_full.py`，覆盖 7 个维度：并发竞争、Worktree 合并策略、端到端 CLI 工作流、Chat 交互式规划、错误恢复与韧性、流式日志解析、CLI + Web API 交叉集成。

**Architecture:** 单文件，7 个 `TestClass` 分组，复用现有 `conftest.py` 的 `cf_project` 和 `claude_subprocess_guard` fixture。所有 Claude CLI 外部调用通过 `ClaudeSubprocessGuard` mock；Git 命令穿透到真实 subprocess 执行 worktree 操作。并发测试使用 `threading.Thread` + 真实 `fcntl.flock` 文件锁。

**Tech Stack:** pytest, unittest.mock, threading, Flask test client, Click CliRunner, fcntl

---

## 现状分析

### 已有测试覆盖

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| `test_integration.py` | 8 | happy path 生命周期、plan 失败重试、chat→plan、worker 执行失败、stdin 隔离 |
| `test_web_api.py` | ~30 | CRUD、approve、chat async、plan、reset、log、respond、retry、status |
| `test_chat.py` | ~30 | ChatMessage/Session 模型、ChatManager 同步/异步消息、finalize |
| 单元测试 (6 files) | ~33 | models/config/task_manager/planner/worktree/worker/cli 各模块独立测试 |

### 关键覆盖缺口（本计划目标）

| # | 缺口 | 当前覆盖 |
|---|------|---------|
| 1 | 多 Worker `claim_next()` 并发竞争、文件锁实际验证 | **零** |
| 2 | `rebase_and_merge`、冲突处理、merge lock、worktree 完整生命周期 | **零**（单元测试仅测 `merge()`） |
| 3 | `cf init → task add → plan → review → run` 全链路 CLI | 仅覆盖 init/task add/status |
| 4 | `cf plan --interactive` → 多轮 chat → finalize → approve 完整状态流转 | **零** |
| 5 | `needs_input` 补充→重执行、reset zombie running、corrupt tasks.json 备份恢复 | **零** |
| 6 | `StreamJsonParser` 解析、结构化日志持久化、`_run_streaming` 超时 | **零** |
| 7 | Web API 操作后 CLI 可见正确状态（数据一致性）| **零** |

---

## Shared Infrastructure

### 复用的 Fixture（来自 `conftest.py`）

- **`cf_project`** — 在 `tmp_path` 下创建含 `.claude-flow/` 完整目录结构的临时 git 仓库
- **`claude_subprocess_guard`** — mock `subprocess.Popen`/`subprocess.run` 拦截 claude CLI 调用，git 命令穿透

### 新增 Helpers（写在测试文件顶部）

```python
def _build_stack(cf_project: Path):
    """构建完整模块栈：Config + TaskManager + Planner + WorktreeManager + Worker。"""
    cfg = Config.load(cf_project)
    cfg.enable_progress_log = False
    tm = TaskManager(cf_project)
    plans_dir = cf_project / ".claude-flow" / "plans"
    planner = Planner(cf_project, plans_dir, cfg, task_manager=tm)
    wt = WorktreeManager(cf_project, cf_project / cfg.worktree_dir)
    worker = Worker(worker_id=0, project_root=cf_project,
                    task_manager=tm, worktree_manager=wt, config=cfg)
    return cfg, tm, planner, wt, worker

def _make_task_approved(tm, planner, guard, title="Test", prompt="Do something"):
    """快速创建一个 approved 状态的任务。"""
    task = tm.add(title, prompt)
    guard.set_plan_output("# Plan\n1. Step one")
    planner.generate(task)
    planner.approve(task)
    tm.update_status(task.id, TaskStatus.APPROVED)
    return task

def _create_flask_client(cf_project):
    """创建 Flask test client。"""
    from claude_flow.web.app import create_app
    cfg = Config.load(cf_project)
    app = create_app(cf_project, cfg)
    app.config["TESTING"] = True
    return app.test_client(), app
```

---

## Task 1: 骨架文件 + Shared Helpers

**Files:**
- Create: `tests/test_integration_full.py`

**Steps:**

1. 创建测试文件，写入所有 import 和上述 helper 函数
2. 创建 7 个空 TestClass 骨架（含 docstring 说明覆盖范围）
3. 运行 `pytest tests/test_integration_full.py --collect-only` 验证文件可被发现

**验证命令:** `pytest tests/test_integration_full.py --collect-only`

**预期结果:** 文件可被 pytest 收集，0 个测试（仅骨架）

---

## Task 2: TestConcurrency — 并发与竞争条件 (4 tests)

**被测模块:** `task_manager.py` (`claim_next`, `_with_lock`), `worktree.py` (`_with_merge_lock`)

**Mock 策略:** 无需 mock claude CLI — 纯 TaskManager + 文件锁测试

### Test 2.1: `test_concurrent_claim_no_duplicate`

**场景:** 3 个线程同时调用 `claim_next()`，有 3 个 approved 任务
**验证:**
- 每个任务只被一个 worker 领取（无重复）
- 所有 3 个任务都被领取
- 领取后状态均为 RUNNING

**实现要点:**
```python
def test_concurrent_claim_no_duplicate(self, cf_project, claude_subprocess_guard):
    cfg, tm, planner, wt, worker = _build_stack(cf_project)
    # 创建 3 个 approved 任务
    tasks = []
    for i in range(3):
        t = _make_task_approved(tm, planner, claude_subprocess_guard,
                                title=f"Task-{i}", prompt=f"Prompt-{i}")
        tasks.append(t)

    claimed = []
    def _claim(wid):
        result = tm.claim_next(wid)
        if result:
            claimed.append((wid, result.id))

    threads = [threading.Thread(target=_claim, args=(i,)) for i in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    # 验证无重复
    claimed_ids = [cid for _, cid in claimed]
    assert len(claimed_ids) == 3
    assert len(set(claimed_ids)) == 3  # 无重复
```

### Test 2.2: `test_concurrent_claim_more_workers_than_tasks`

**场景:** 5 个线程竞争 2 个 approved 任务
**验证:**
- 恰好 2 个 worker 获得任务
- 其余 3 个返回 None
- 无异常抛出

### Test 2.3: `test_priority_ordering_under_contention`

**场景:** 创建 3 个不同优先级的 approved 任务（P=10, P=5, P=1），单线程顺序 claim
**验证:**
- 第一次 claim 得到 P=10 的任务
- 第二次 claim 得到 P=5 的任务
- 第三次 claim 得到 P=1 的任务

**实现要点:** 使用 `tm.add(title, prompt, priority=N)`，然后依次 approve 并 claim

### Test 2.4: `test_concurrent_read_write_safety`

**场景:** 1 个线程持续写入（add + update_status），3 个线程持续读取（list_tasks, get），并发运行 100 次
**验证:**
- 无 `json.JSONDecodeError` 异常
- 无 `FileNotFoundError` 异常
- 最终数据一致（tasks.json 可正常解析）

**实现要点:**
```python
errors = []
def _writer():
    for i in range(20):
        t = tm.add(f"W-{i}", f"prompt-{i}")
        tm.update_status(t.id, TaskStatus.APPROVED)
def _reader():
    for _ in range(30):
        try:
            tm.list_tasks()
        except Exception as e:
            errors.append(e)
# 启动 1 writer + 3 reader 线程
# 验证 errors 为空
```

**验证命令:** `pytest tests/test_integration_full.py::TestConcurrency -v`

---

## Task 3: TestWorktreeMerge — Worktree 合并策略 (7 tests)

**被测模块:** `worktree.py` (`create`, `remove`, `merge`, `rebase_and_merge`, `_with_merge_lock`, `cleanup_all`)

**Mock 策略:** Git 命令穿透到真实 subprocess；Claude CLI（冲突解决）mock

### Test 3.1: `test_worktree_create_and_remove_lifecycle`

**场景:** 创建 worktree → 验证目录和分支存在 → 移除 → 验证清理完毕
**验证:**
- `wt.create(task_id, branch)` 返回的路径存在且是目录
- `git branch --list` 包含该分支
- `wt.remove()` 后目录不存在，分支已删除

### Test 3.2: `test_merge_no_ff_success`

**场景:** 在 worktree 中创建一个 commit → 使用 `merge(branch, "main", "--no-ff")` 合并
**验证:**
- 合并返回 True
- main 分支的 `git log` 包含 merge commit
- worktree 中的文件变更已出现在 main 分支

**实现要点:**
```python
wt_path = wt.create("task-001", "cf/task-001")
(wt_path / "feature.txt").write_text("new feature")
subprocess.run(["git", "add", "."], cwd=str(wt_path), check=True, capture_output=True)
subprocess.run(["git", "commit", "-m", "feat: add feature"],
               cwd=str(wt_path), check=True, capture_output=True)
success = wt.merge("cf/task-001", "main", "--no-ff")
assert success is True
# 验证 main 分支包含 feature.txt
assert (cf_project / "feature.txt").exists()
```

### Test 3.3: `test_merge_conflict_returns_false`

**场景:** main 和 worktree 分支修改同一文件的同一行 → merge 产生冲突
**验证:**
- `merge()` 返回 False
- main 分支未被污染（仍在 merge 前的状态）
- `git merge --abort` 已自动执行

**实现要点:**
```python
# 在 main 上修改 README.md 第一行
(cf_project / "README.md").write_text("main version")
subprocess.run(["git", "add", "."], cwd=str(cf_project), ...)
subprocess.run(["git", "commit", "-m", "main change"], cwd=str(cf_project), ...)
# 在 worktree 上修改 README.md 第一行
(wt_path / "README.md").write_text("branch version")
subprocess.run(["git", "add", "."], cwd=str(wt_path), ...)
subprocess.run(["git", "commit", "-m", "branch change"], cwd=str(wt_path), ...)
# 合并
success = wt.merge("cf/task-002", "main", "--no-ff")
assert success is False
```

### Test 3.4: `test_rebase_and_merge_success`

**场景:** main 有新 commit，worktree 分支也有 commit，无冲突 → rebase 后 ff-only 合并
**验证:**
- `rebase_and_merge()` 返回 True
- main 分支包含两方的变更
- git log 是线性的（无 merge commit）

### Test 3.5: `test_rebase_and_merge_conflict_abort`

**场景:** main 和 worktree 修改同一文件同一行 → rebase 冲突 → 无 skip_permissions → 无法调用 claude 自动解决 → abort
**验证:**
- `rebase_and_merge()` 返回 False
- `git rebase --abort` 已执行
- worktree 仍可用（未处于中断 rebase 状态）

**实现要点:** 使用 `config` 参数但 `skip_permissions=False`（因为非 root 或其他条件不满足 `can_skip_permissions`），claude 冲突解决不会被调用

### Test 3.6: `test_merge_lock_serialization`

**场景:** 2 个线程同时调用 `merge()`，验证 `_with_merge_lock` 串行化
**验证:**
- 两次 merge 都成功完成
- 无并发异常（`merge.lock` 文件正确加锁释放）

**实现要点:**
```python
# 创建 2 个 worktree 各有独立变更
# 2 个线程同时调用 wt.merge()
# 验证 main 分支最终包含两方变更
```

### Test 3.7: `test_cleanup_all`

**场景:** 创建 3 个 worktree → `cleanup_all()` → 验证全部清理
**验证:**
- `cleanup_all()` 返回 3
- `list_active()` 返回空列表
- worktree 目录已删除

**验证命令:** `pytest tests/test_integration_full.py::TestWorktreeMerge -v`

---

## Task 4: TestCLIWorkflow — 端到端 CLI 工作流 (8 tests)

**被测模块:** `cli.py` 全部命令

**Mock 策略:** 使用 `click.testing.CliRunner`，claude CLI mock via `claude_subprocess_guard`

### Test 4.1: `test_full_cli_lifecycle`

**场景:** `cf init` → `cf task add` → `cf plan -t <id> -F` → `cf plan approve <id>` → `cf run <id>` → `cf status`
**验证:**
- 每个命令的 exit_code == 0
- 状态正确流转：pending → planning → planned → approved → running → done
- `cf status` 输出包含 "done: 1"

**实现要点:**
```python
runner = CliRunner()
with runner.isolated_filesystem():
    # 在 cf_project 目录下运行
    env = {"CF_PROJECT_ROOT": str(cf_project)}
    result = runner.invoke(main, ["init"], env=env)
    assert result.exit_code == 0
    result = runner.invoke(main, ["task", "add", "Feature X", "-p", "Add X"], env=env)
    assert result.exit_code == 0
    # 提取 task_id
    task_id = ...  # parse from output
    result = runner.invoke(main, ["plan", "-t", task_id, "-F"], env=env)
    ...
```

### Test 4.2: `test_task_add_and_list`

**场景:** 添加 3 个不同优先级的任务 → `cf task list`
**验证:**
- 列表按优先级降序排列
- 输出包含所有 3 个任务的 ID 和标题

### Test 4.3: `test_task_show_details`

**场景:** 添加任务 → `cf task show <id>`
**验证:**
- 输出包含 ID、Title、Status、Priority、Prompt 等字段

### Test 4.4: `test_task_remove`

**场景:** 添加任务 → `cf task remove <id>` → `cf task list`
**验证:**
- remove 输出 "Removed"
- list 不再包含该任务

### Test 4.5: `test_reset_failed_task`

**场景:** 任务执行失败 → `cf reset <id>` → 验证状态回到 pending
**验证:**
- reset 输出 "Reset ... to pending"
- `cf task show <id>` 显示 status: pending

### Test 4.6: `test_retry_all_failed`

**场景:** 2 个任务失败 → `cf retry` → 验证状态变为 approved
**验证:**
- 输出 "Retrying 2 tasks"
- 两个任务状态均为 approved

### Test 4.7: `test_log_view`

**场景:** 任务执行完成后 → `cf log <id>`
**验证:**
- 输出包含日志内容（raw log 或 structured log 格式化后）

### Test 4.8: `test_clean_worktrees`

**场景:** 创建 worktree → `cf clean` → 验证清理
**验证:**
- 输出 "Cleaned N worktrees"
- worktree 目录已清空

**验证命令:** `pytest tests/test_integration_full.py::TestCLIWorkflow -v`

---

## Task 5: TestChatPlanning — Chat 交互式规划 (7 tests)

**被测模块:** `chat.py` (`ChatManager`, `ChatSession`), `planner.py` (`generate_from_chat`), `cli.py` (plan --interactive, plan chat, plan finalize)

**Mock 策略:** Claude CLI mock（`subprocess.run` 和 `subprocess.Popen`）

### Test 5.1: `test_chat_session_lifecycle`

**场景:** 创建 session → 验证 active → finalize → 验证 finalized
**验证:**
- `create_session()` 返回 active 状态的 session
- `finalize()` 后 status == "finalized"
- session JSON 文件存在于 `.claude-flow/chats/`

### Test 5.2: `test_initial_prompt_generates_analysis`

**场景:** `send_initial_prompt()` 发送任务描述 → AI 返回初始分析
**验证:**
- 返回非空字符串
- session.messages 包含 1 条 assistant 消息
- Claude CLI 被调用且 prompt 包含任务描述

### Test 5.3: `test_multi_round_conversation`

**场景:** 发送 3 轮用户消息，每轮 AI 回复不同内容
**验证:**
- session.messages 长度 == 6（3 user + 3 assistant）
- 每轮 AI 回复与 mock 输出一致
- 历史消息在后续 prompt 中被正确包含

**实现要点:**
```python
# Mock claude CLI 返回不同内容
with patch("claude_flow.chat.subprocess.run") as mock_run:
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="Response 1", stderr=""),
        MagicMock(returncode=0, stdout="Response 2", stderr=""),
        MagicMock(returncode=0, stdout="Response 3", stderr=""),
    ]
    chat_mgr.send_message(task.id, "Q1", task_prompt=task.prompt)
    chat_mgr.send_message(task.id, "Q2", task_prompt=task.prompt)
    chat_mgr.send_message(task.id, "Q3", task_prompt=task.prompt)
```

### Test 5.4: `test_async_message_thinking_flag`

**场景:** `send_message_async()` → 立即检查 thinking=True → 等待完成 → thinking=False
**验证:**
- 调用后立即 `get_session()` 返回 `thinking=True`
- 等待线程完成后 `get_session()` 返回 `thinking=False`
- AI 回复已追加到 messages

### Test 5.5: `test_finalize_and_generate_plan_from_chat`

**场景:** 多轮对话后 → `finalize()` → `planner.generate_from_chat()` → 验证计划文件
**验证:**
- session status == "finalized"
- 计划文件存在且内容包含 YAML front matter
- task status == PLANNED
- 计划文件路径记录在 `task.plan_file`

### Test 5.6: `test_stale_thinking_recovery_on_startup`

**场景:** 手动将 session JSON 的 `thinking` 设为 True → 新建 `ChatManager` → 验证自动恢复
**验证:**
- 新实例的 `get_session()` 返回 `thinking=False`
- messages 末尾有 "[System] AI response was interrupted" 消息

**实现要点:**
```python
# 手动写入 stale session
session_path = cf_project / ".claude-flow" / "chats" / f"{task.id}.json"
data = {"task_id": task.id, "mode": "interactive", "status": "active",
        "thinking": True, "messages": [{"role": "user", "content": "hello", "timestamp": "..."}]}
session_path.write_text(json.dumps(data))
# 创建新 ChatManager（触发 _recover_stale_sessions）
new_mgr = ChatManager(cf_project, cfg)
session = new_mgr.get_session(task.id)
assert session.thinking is False
assert "interrupted" in session.messages[-1].content
```

### Test 5.7: `test_abort_session_kills_subprocess`

**场景:** `send_message_async()` 启动后台线程 → 立即 `abort_session()` → 验证清理
**验证:**
- session 文件被删除
- `get_session()` 返回 None
- 无残留线程（`_active_threads` 中无该 task_id）

**验证命令:** `pytest tests/test_integration_full.py::TestChatPlanning -v`

---

## Task 6: TestErrorRecovery — 错误恢复与韧性 (7 tests)

**被测模块:** `task_manager.py` (`respond`, `update_status`, `_load_from_backup`), `worker.py` (`execute_task`), `cli.py` (`reset`, `respond`)

### Test 6.1: `test_needs_input_respond_and_reexecute`

**场景:** Worker 执行 → 无代码变更 → 状态变为 needs_input → `tm.respond()` 补充信息 → 重新 claim → 成功执行
**验证:**
- 第一次执行后 status == NEEDS_INPUT
- respond 后 status == APPROVED，prompt 包含补充信息
- 第二次执行后 status == DONE

**实现要点:**
```python
# 第一次执行：mock claude 不产生文件变更
guard.set_task_output('{"type":"result","result":"I need more info about X"}')
# 注意：不在 worktree 中创建文件，使 _auto_commit 返回 False 且 _has_new_commits 返回 False
# 需要 patch ClaudeSubprocessGuard 让 mock_popen 不写文件
```

### Test 6.2: `test_reset_zombie_running_task`

**场景:** 任务状态为 RUNNING（worker 崩溃后残留）→ 有 plan_file → `reset` → 状态回到 PLANNED → worktree 清理
**验证:**
- reset 前 status == RUNNING
- reset 后 status == PLANNED（因为有 plan_file）
- orphaned worktree 和分支已清理

**实现要点:**
```python
# 手动设置任务为 RUNNING + 创建 worktree
task = tm.add("Zombie", "prompt")
tm.update_status(task.id, TaskStatus.PLANNED)
# 模拟 plan_file 存在
plans_dir = cf_project / ".claude-flow" / "plans"
(plans_dir / f"{task.id}.md").write_text("# Plan")
# 手动更新 plan_file 字段...
tm.update_status(task.id, TaskStatus.APPROVED)
claimed = tm.claim_next(0)
# 此时 status=RUNNING, branch=cf/{task_id}
# worktree 可能已创建...
# 模拟 worker crash（不执行 execute_task）
# 调用 CLI reset
runner.invoke(main, ["reset", claimed.id], env=...)
```

### Test 6.3: `test_corrupt_tasks_json_backup_recovery`

**场景:** 正常操作产生 tasks.json → 手动损坏 tasks.json 内容 → 下次 load 自动从 backup 恢复
**验证:**
- 损坏前正常操作
- 损坏 tasks.json 后 `tm.list_tasks()` 仍返回正确数据（从 backup）
- tasks.json 被自动修复

**实现要点:**
```python
# 添加几个任务（产生 backup）
tm.add("T1", "P1")
tm.add("T2", "P2")
# 确认 backup 存在
backup = cf_project / ".claude-flow" / "tasks.json.bak"
assert backup.exists()
# 损坏 main file
(cf_project / ".claude-flow" / "tasks.json").write_text("{corrupt!!!}")
# 重新创建 TaskManager（绕过内存缓存）
tm2 = TaskManager(cf_project)
tasks = tm2.list_tasks()
assert len(tasks) >= 1  # 从 backup 恢复
```

### Test 6.4: `test_worker_exception_marks_failed`

**场景:** Worker 执行中抛出未预期异常 → 任务标记为 FAILED
**验证:**
- status == FAILED
- error 包含异常信息

**实现要点:** Mock `wt.create()` 抛出 `subprocess.CalledProcessError`

### Test 6.5: `test_worker_timeout_marks_failed`

**场景:** 配置 `task_timeout=1` → Worker 执行超时
**验证:**
- status == FAILED
- error 包含 "Timeout"
- worktree 被清理

**实现要点:**
```python
cfg.task_timeout = 1  # 1 秒超时
# Mock Popen 使其 stdout 阻塞（不产生任何输出）
# 或让 _run_streaming 的超时检查触发
```

### Test 6.6: `test_pre_merge_test_failure_marks_failed`

**场景:** 配置 `pre_merge_commands=["exit 1"]` → Worker 执行成功但合并前测试失败
**验证:**
- status == FAILED
- error 包含 "Pre-merge tests failed"
- worktree 被清理

### Test 6.7: `test_empty_tasks_file_recovery`

**场景:** tasks.json 存在但为空文件 → load 不报错 → 尝试从 backup 恢复
**验证:**
- 不抛出异常
- 如果 backup 存在则恢复数据
- 如果 backup 也不存在则返回空列表

**验证命令:** `pytest tests/test_integration_full.py::TestErrorRecovery -v`

---

## Task 7: TestStreamingLogs — 流式日志解析 (9 tests)

**被测模块:** `monitor.py` (`StreamJsonParser`, `StreamEvent`, `format_structured_log_for_cli`)

**Mock 策略:** 无需 mock — 纯逻辑测试

### Test 7.1: `test_parse_tool_use_event`

**场景:** 解析 `{"type":"tool_use","tool":"Read","input":{"file_path":"/tmp/x.py"}}` 行
**验证:**
- 返回 StreamEvent，event_type == "tool_use"
- content 包含 "Read" 和文件路径

### Test 7.2: `test_parse_tool_result_success`

**场景:** 解析 `{"type":"tool_result","tool":"Write","is_error":false}` 行
**验证:**
- event_type == "tool_use"（成功的 tool_result 归类为 tool_use）
- content 包含 "Write: ok"

### Test 7.3: `test_parse_tool_result_error`

**场景:** 解析 `{"type":"tool_result","tool":"Bash","is_error":true}` 行
**验证:**
- event_type == "error"
- content 包含 "Bash: ERROR"

### Test 7.4: `test_parse_assistant_message_with_content_array`

**场景:** 解析包含 text + tool_use 的 assistant 消息
```json
{"type":"assistant","message":{"content":[{"type":"text","text":"Let me check"},{"type":"tool_use","name":"Read","input":{"file_path":"x.py"}}]}}
```
**验证:**
- 产生 2 个事件：1 text + 1 tool_use
- text 事件 content == "Let me check"
- tool_use 事件 content 包含 "Read"

### Test 7.5: `test_parse_result_with_cost`

**场景:** 解析 `{"type":"result","result":"done","cost_usd":0.0234}` 行
**验证:**
- event_type == "result"
- content 包含 "$0.0234"
- raw["cost_usd"] == 0.0234

### Test 7.6: `test_get_summary_counts`

**场景:** 解析多行混合事件 → `get_summary()`
**验证:**
- tool_use 计数正确
- error 计数正确
- total 计数正确

### Test 7.7: `test_to_structured_log`

**场景:** 解析多行事件 → `to_structured_log("task-abc")`
**验证:**
- 返回 dict 包含 task_id, summary, cost_usd, events
- events 是 list of dict，每个包含 type, ts, content
- cost_usd 从最后一个 result 事件提取

### Test 7.8: `test_format_structured_log_for_cli`

**场景:** 构造 structured log dict → `format_structured_log_for_cli()`
**验证:**
- 返回非空字符串
- 包含 task_id
- 包含 tool 和 error 计数
- 包含 ANSI 颜色代码

### Test 7.9: `test_invalid_json_lines_silently_skipped`

**场景:** 解析包含非 JSON 行、空行、无 type 字段的行
**验证:**
- `parse_line()` 返回 None
- 不抛出异常
- `get_events()` 不包含无效数据

**验证命令:** `pytest tests/test_integration_full.py::TestStreamingLogs -v`

---

## Task 8: TestCLIWebCross — CLI + Web API 交叉集成 (7 tests)

**被测模块:** `web/api.py` + `cli.py` + `task_manager.py` 共享数据层

**Mock 策略:** Flask test client + CliRunner，共享同一个 `cf_project`

### Test 8.1: `test_web_create_cli_visible`

**场景:** 通过 Web API 创建任务 → CLI `cf task list` 可见
**验证:**
- Web POST `/api/tasks` 成功
- `CliRunner.invoke(main, ["task", "list"])` 输出包含该任务标题

**实现要点:**
```python
client, app = _create_flask_client(cf_project)
resp = client.post("/api/tasks", json={"title": "WebTask", "prompt": "Do X"})
task_id = resp.get_json()["data"]["id"]
# CLI 验证
runner = CliRunner()
env = {"CF_PROJECT_ROOT": str(cf_project)}
result = runner.invoke(main, ["task", "list"], env=env)
assert "WebTask" in result.output
```

### Test 8.2: `test_cli_create_web_visible`

**场景:** 通过 CLI 创建任务 → Web API `GET /api/tasks` 可见
**验证:**
- CLI task add 成功
- Web GET `/api/tasks` 返回的列表包含该任务

### Test 8.3: `test_web_approve_cli_status`

**场景:** CLI 创建 + plan → Web API approve → CLI status 显示 approved
**验证:**
- Web POST `/api/tasks/{id}/approve` 返回 ok
- CLI `cf task show <id>` 显示 "approved"

### Test 8.4: `test_web_respond_needs_input`

**场景:** 任务进入 needs_input → Web API POST `/api/tasks/{id}/respond` → CLI 可见 approved
**验证:**
- Web respond 返回 ok
- CLI `cf task show <id>` 显示 approved
- prompt 包含补充信息

### Test 8.5: `test_web_reset_zombie_running`

**场景:** 任务状态为 RUNNING（zombie）→ Web API POST `/api/tasks/{id}/reset` → 状态正确重置
**验证:**
- 有 plan_file 时重置为 PLANNED
- 无 plan_file 时重置为 PENDING

### Test 8.6: `test_global_status_consistency`

**场景:** 通过 CLI 和 Web 混合操作多个任务 → Web GET `/api/status` → CLI `cf status`
**验证:**
- Web status 的 counts 与 CLI status 的输出一致

### Test 8.7: `test_web_batch_delete_cli_confirms`

**场景:** CLI 创建 3 个任务 → Web API 批量删除 2 个 → CLI list 只剩 1 个
**验证:**
- Web batch-delete 返回 deleted: 2
- CLI task list 只显示 1 个任务

**验证命令:** `pytest tests/test_integration_full.py::TestCLIWebCross -v`

---

## 执行策略

### 执行顺序

按 Task 编号顺序执行。每个 Task 完成后运行验证命令确认通过。

### 每个 Task 的工作流

1. **写测试** — 在 `test_integration_full.py` 中实现该 Task 的所有测试方法
2. **运行验证** — `pytest tests/test_integration_full.py::TestXxx -v`
3. **修复** — 如果有失败，分析原因并修复测试代码（不修改源码，除非发现真实 bug）
4. **确认全量** — `pytest tests/test_integration_full.py -v` 确认无回归

### 最终验证

```bash
# 全部集成测试
pytest tests/test_integration_full.py -v

# 全量测试（确保不影响现有测试）
pytest -v

# 覆盖率（可选）
pytest --cov=claude_flow tests/test_integration_full.py -v
```

### 测试数量汇总

| Task | TestClass | 测试数 |
|------|-----------|--------|
| 2 | TestConcurrency | 4 |
| 3 | TestWorktreeMerge | 7 |
| 4 | TestCLIWorkflow | 8 |
| 5 | TestChatPlanning | 7 |
| 6 | TestErrorRecovery | 7 |
| 7 | TestStreamingLogs | 9 |
| 8 | TestCLIWebCross | 7 |
| **Total** | **7 classes** | **49 tests** |

---

## 注意事项

1. **Git 操作穿透** — `conftest.py` 的 `ClaudeSubprocessGuard` 仅 mock claude CLI 和 shell 命令，git 命令走真实 subprocess。TestWorktreeMerge 依赖此行为。

2. **线程安全** — 并发测试使用 `threading.Thread`，不使用 `multiprocessing`（避免 fixture 隔离问题）。`fcntl.flock` 在线程间也有效。

3. **Fixture 隔离** — 每个测试使用独立的 `cf_project`（基于 `tmp_path`），无测试间状态泄露。

4. **Web 测试需要 Flask** — TestCLIWebCross 需要 `flask` 依赖。如果未安装，该 TestClass 应被标记为 `pytest.mark.skipif`。

5. **`needs_input` 测试** — 需要 mock `ClaudeSubprocessGuard` 不在 worktree 中创建文件（覆盖默认行为），使 Worker 检测到"无代码变更"进入 needs_input 流程。

6. **超时测试** — `test_worker_timeout_marks_failed` 需要设置 `cfg.task_timeout=1`，mock Popen 使 `_run_streaming` 中的超时检查触发。建议让 mock stdout 阻塞等待（`time.sleep` 在 mock 中）。
