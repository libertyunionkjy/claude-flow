# Token Usage Statistics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 Claude Flow 添加全量 token 用量统计功能，覆盖所有 Claude CLI 调用点（方案 C），支持按 task、总计、每日、每月维度查看用量和费用。

**Architecture:** 将所有 Claude CLI 调用统一为 `stream-json` 输出格式，通过通用的 `run_claude()` 函数集中采集 token 数据。每次 Claude 调用完成后，解析 `result` 事件中的 `usage` 和 `modelUsage` 字段，写入 `.claude-flow/usage.jsonl` 追加式日志。新增 `UsageManager` 负责 JSONL 读取、聚合和查询，`cf usage` CLI 命令组提供多维度展示。

**Tech Stack:** Python 3.10+, dataclass, JSON/JSONL, Click CLI, fcntl (file lock)

---

## 数据模型设计

### stream-json result 事件中的 token 字段

```json
{
  "type": "result",
  "session_id": "be7038be-...",
  "total_cost_usd": 0.7486,
  "duration_ms": 111466,
  "usage": {
    "input_tokens": 32468,
    "cache_creation_input_tokens": 56842,
    "cache_read_input_tokens": 165883,
    "output_tokens": 5921
  },
  "modelUsage": {
    "Claude-Opus-4.6": {
      "inputTokens": 32468,
      "outputTokens": 5921,
      "cacheReadInputTokens": 165883,
      "cacheCreationInputTokens": 56842,
      "costUSD": 0.7486
    }
  }
}
```

### UsageRecord dataclass

```python
@dataclass
class UsageRecord:
    timestamp: datetime           # 记录时间
    task_id: Optional[str]        # 关联的 task ID（planner/chat 调用可能没有对应 task）
    session_id: Optional[str]     # Claude Code session ID
    call_type: str                # "task_exec" | "test_fix" | "plan_auto" | "plan_chat" |
                                  # "chat_init" | "chat_msg" | "chat_async" | "rebase" | "progress"
    models: dict[str, dict]       # modelUsage 数据（按模型名）
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    total_tokens: int             # = input + output + cache_creation + cache_read
    cost_usd: float
    duration_ms: Optional[int]
```

---

## 任务分解

### Task 1: UsageRecord 数据模型 + UsageStore JSONL 存储

**File:** `claude_flow/usage.py` (新建)

**实现内容：**

1. `UsageRecord` dataclass，包含上述字段
   - `to_dict()` 序列化方法（datetime -> ISO string）
   - `from_dict()` 反序列化方法
   - `total_tokens` 属性 = input + output + cache_creation + cache_read

2. `UsageStore` 类：JSONL 追加式存储
   - `__init__(self, project_root: Path)` -- 路径为 `.claude-flow/usage.jsonl`
   - `append(record: UsageRecord)` -- 使用 `fcntl.flock` 文件锁追加一行 JSON
   - `load_all() -> list[UsageRecord]` -- 逐行读取 JSONL，跳过损坏行
   - `load_range(start: datetime, end: datetime) -> list[UsageRecord]` -- 按时间范围过滤

**设计约束：**
- 追加写入避免读改写全量文件
- 文件锁使用 `fcntl.flock(LOCK_EX)`，与 `TaskManager._with_lock` 保持一致
- JSONL 格式：每行一个完整 JSON 对象，便于流式追加

**依赖：** 无

---

### Task 2: UsageManager 聚合查询

**File:** `claude_flow/usage.py` (续)

**实现内容：**

`UsageManager` 类，基于 `UsageStore` 提供聚合查询：

```python
class UsageManager:
    def __init__(self, project_root: Path):
        self._store = UsageStore(project_root)

    def get_task_usage(self, task_id: str) -> dict:
        """按 task_id 聚合用量"""
        # 返回 {input_tokens, output_tokens, cache_creation, cache_read, total, cost_usd, sessions: [...]}

    def get_total_usage(self) -> dict:
        """全局总用量"""
        # 返回 {input_tokens, output_tokens, ..., cost_usd, model_breakdown: {model_name: {...}}}

    def get_daily_usage(self, days: int = 30) -> list[dict]:
        """按日聚合"""
        # 返回 [{date: "2026-03-09", input_tokens: ..., cost_usd: ...}, ...]

    def get_monthly_usage(self, months: int = 12) -> list[dict]:
        """按月聚合"""
        # 返回 [{month: "2026-03", input_tokens: ..., cost_usd: ...}, ...]

    def get_model_breakdown(self) -> dict[str, dict]:
        """按模型分组"""
        # 返回 {"Claude-Opus-4.6": {input: ..., output: ..., cost: ...}, ...}
```

**聚合输出格式（表格展示用）：**
```
Session     | Models             |   Input |  Output | Cache Create | Cache Read | Total Tokens | Cost (USD)
------------+--------------------+---------+---------+--------------+------------+--------------+-----------
be7038be... | Claude-Opus-4.6    |  32,468 |   5,921 |       56,842 |    165,883 |      261,114 |     $0.75
```

**依赖：** Task 1

---

### Task 3: stream-json 解析工具函数 + 通用 `run_claude()` 函数

**File:** `claude_flow/usage.py` (续)

**实现内容：**

1. `extract_usage_from_stream_json(output: str) -> Optional[UsageRecord]`
   - 逐行扫描 stream-json 输出，找到 `type: "result"` 行
   - 提取 `usage`、`modelUsage`、`total_cost_usd`、`session_id`、`duration_ms`
   - 构造并返回 `UsageRecord`（`task_id` 和 `call_type` 由调用方填入）

2. `extract_text_from_stream_json(output: str) -> str`
   - 逐行扫描 stream-json 输出，收集所有 `type: "assistant"` 中的 text content
   - 拼接为完整文本返回
   - 用于替代 `--output-format text` 的直接文本输出

3. `run_claude(cmd, *, cwd, call_type, task_id, project_root, timeout, **kwargs) -> tuple[str, int]`
   - 统一封装所有 Claude CLI 调用
   - 执行 `subprocess.Popen`，捕获完整 stdout
   - 自动注入 `--output-format stream-json` 替换 `--output-format text`
   - 调用 `extract_usage_from_stream_json()` 提取用量
   - 调用 `extract_text_from_stream_json()` 提取文本内容
   - 调用 `UsageStore.append()` 写入 JSONL
   - 返回 `(text_output, returncode)`
   - 支持 `KeyboardInterrupt` 优雅中断（与 `Planner._run_claude` 一致）

**关键设计决策：**
- `run_claude()` 作为所有 Claude CLI 调用的唯一入口，替代分散在各模块的 `subprocess.run/Popen` 调用
- 内部自动注入 `--output-format stream-json`，调用方无需关心格式
- `extract_text_from_stream_json()` 确保从 stream-json 中提取文本的行为与原 `--output-format text` 等价

**依赖：** Task 1

---

### Task 4: 改造 `planner.py` -- 统一 stream-json

**File:** `claude_flow/planner.py`

**改动范围：** `Planner._run_claude()`, `Planner.generate()`, `Planner.generate_from_chat()`

**具体变更：**

1. **`_run_claude()`** (planner.py:35-66)
   - 替换为调用 `usage.run_claude()`
   - 不再自行管理 `Popen`
   - `call_type`: generate -> `"plan_auto"`, generate_from_chat -> `"plan_chat"`

2. **`generate()`** (planner.py:72-101)
   - `cmd` 移除 `"--print", "--output-format", "text"`（`run_claude()` 自动处理）
   - `result.stdout` 改为 `run_claude()` 返回的 text_output
   - `task_id` 从 `task.id` 获取

3. **`generate_from_chat()`** (planner.py:113-182)
   - 同上，替换 cmd 和结果提取

**影响面：**
- `planner.py` 的 2 个 Claude CLI 调用点全部改造
- 不改变外部接口签名

**依赖：** Task 3

---

### Task 5: 改造 `chat.py` -- 统一 stream-json

**File:** `claude_flow/chat.py`

**改动范围：** `ChatManager.send_initial_prompt()`, `ChatManager.send_message()`, `ChatManager._async_claude_call()`, `ChatManager._build_cmd()`

**具体变更：**

1. **`_build_cmd()`** (chat.py:369-374)
   - 移除 `"--print", "--output-format", "text"`
   - 保留 `["claude", "-p", prompt]` + skip_permissions
   - 或者直接在调用处使用 `run_claude()` 替代

2. **`send_initial_prompt()`** (chat.py:194-245)
   - `subprocess.run()` 替换为 `usage.run_claude()`
   - `call_type = "chat_init"`, `task_id = task_id`
   - `result.stdout` -> text_output

3. **`send_message()`** (chat.py:247-299)
   - 同上，`call_type = "chat_msg"`

4. **`_async_claude_call()`** (chat.py:376-407)
   - `subprocess.run()` 替换为 `usage.run_claude()`
   - `call_type = "chat_async"`

**影响面：**
- `chat.py` 的 3 个 Claude CLI 调用点全部改造
- 异步调用需注意 `run_claude()` 的线程安全性（`UsageStore.append` 有文件锁保护，无问题）

**依赖：** Task 3

---

### Task 6: 改造 `progress.py` -- 统一 stream-json

**File:** `claude_flow/progress.py`

**改动范围：** `ProgressLogger.log_success()` 中的 Claude CLI 调用 (progress.py:59-83)

**具体变更：**

1. **`log_success()`**
   - `subprocess.run(cmd, ...)` 替换为 `usage.run_claude()`
   - `call_type = "progress"`, `task_id = task.id`
   - `result.stdout.strip()` -> text_output

**影响面：**
- `progress.py` 的 1 个 Claude CLI 调用点改造
- 该调用使用的超时是硬编码 120s，改造时应传递给 `run_claude()`

**依赖：** Task 3

---

### Task 7: 改造 `worktree.py` -- 统一 stream-json

**File:** `claude_flow/worktree.py`

**改动范围：** `WorktreeManager.rebase_and_merge()` 中的 Claude CLI 调用 (worktree.py:197-200)

**具体变更：**

1. **`rebase_and_merge()`**
   - Claude CLI 调用（解决冲突）替换为 `usage.run_claude()`
   - `call_type = "rebase"`, `task_id` 从 branch 名提取（`cf/{task_id}` -> `task_id`）
   - 注意：此调用的 `cwd` 是 worktree 路径，非项目根目录
   - `project_root` 需要传入（用于写 JSONL），可从 `self._repo` 获取

**影响面：**
- `worktree.py` 的 1 个 Claude CLI 调用点改造
- 需要在 `run_claude()` 中区分 `cwd`（执行目录）和 `project_root`（JSONL 存储目录）

**依赖：** Task 3

---

### Task 8: 改造 `worker.py` -- 统一 stream-json 采集

**File:** `claude_flow/worker.py`

**改动范围：** `Worker._execute_task_inner()` 和 `Worker._run_pre_merge_tests()`

**具体变更：**

1. **主任务执行** (worker.py:68-85)
   - 当前已使用 `stream-json`，通过 `_run_streaming()` 管理 Popen
   - **推荐方案：保持 `_run_streaming()` 不变，在返回后追加 usage 采集**
   - 在 `_run_streaming()` 末尾（`parser.get_summary()` 之后），从 parser 事件中提取 result 的 usage

```python
# 在 _run_streaming() 的 finally 或正常结束后：
for event in reversed(parser.get_events()):
    if event.event_type == "result" and event.raw.get("usage"):
        record = UsageRecord(
            timestamp=datetime.now(),
            task_id=task.id,
            session_id=event.raw.get("session_id"),
            call_type="task_exec",
            models=event.raw.get("modelUsage", {}),
            input_tokens=event.raw["usage"].get("input_tokens", 0),
            output_tokens=event.raw["usage"].get("output_tokens", 0),
            cache_creation_tokens=event.raw["usage"].get("cache_creation_input_tokens", 0),
            cache_read_tokens=event.raw["usage"].get("cache_read_input_tokens", 0),
            cost_usd=event.raw.get("total_cost_usd", 0),
            duration_ms=event.raw.get("duration_ms"),
        )
        record.total_tokens = (record.input_tokens + record.output_tokens
                               + record.cache_creation_tokens + record.cache_read_tokens)
        UsageStore(self._root).append(record)
        break
```

2. **测试修复** (worker.py:239-247)
   - `subprocess.run(fix_cmd, ...)` 替换为 `usage.run_claude()`
   - `call_type = "test_fix"`, `task_id = task.id`

**依赖：** Task 3

---

### Task 9: CLI `cf usage` 命令组

**File:** `claude_flow/cli.py`

**新增命令：**

```
cf usage                          # 总用量摘要（默认）
cf usage task <task_id>           # 单个 task 的用量
cf usage daily [--days N]         # 按日统计（默认 30 天）
cf usage monthly [--months N]    # 按月统计（默认 12 个月）
cf usage models                   # 按模型分组统计
cf usage export [--format csv|json] [--output file]  # 导出
```

**输出格式（ASCII 表格）：**

```
=== Claude Flow Usage Summary ===

Total Cost: $12.45
Total Tokens: 1,234,567

Session     | Models             |    Input |   Output | Cache Create | Cache Read | Total Tokens | Cost (USD)
------------+--------------------+----------+----------+--------------+------------+--------------+-----------
task-a1b2c3 | Claude-Opus-4.6    |   32,468 |    5,921 |       56,842 |    165,883 |      261,114 |     $0.75
task-d4e5f6 | Claude-Sonnet-4.6  |   12,345 |    2,100 |       20,000 |     80,000 |      114,445 |     $0.12
------------+--------------------+----------+----------+--------------+------------+--------------+-----------
Total       |                    |   44,813 |    8,021 |       76,842 |    245,883 |      375,559 |     $0.87
```

**实现要点：**
- 使用 `click.echo()` 输出，不依赖外部表格库
- 数字格式化：千分位分隔符
- 费用格式化：`$X.XXXX`
- 空数据时显示友好提示

**依赖：** Task 2

---

### Task 10: Web API `/api/usage` 端点

**File:** `claude_flow/web/api.py`

**新增端点：**

```
GET /api/usage                    # 总用量摘要
GET /api/usage/task/<task_id>     # 单个 task 用量
GET /api/usage/daily?days=30      # 按日统计
GET /api/usage/monthly?months=12  # 按月统计
GET /api/usage/models             # 按模型分组
```

**响应格式：**
```json
{
  "ok": true,
  "data": {
    "total_cost_usd": 12.45,
    "total_tokens": 1234567,
    "input_tokens": 500000,
    "output_tokens": 100000,
    "cache_creation_tokens": 300000,
    "cache_read_tokens": 334567,
    "model_breakdown": {
      "Claude-Opus-4.6": { "cost_usd": 10.00, "total_tokens": 900000 },
      "Claude-Sonnet-4.6": { "cost_usd": 2.45, "total_tokens": 334567 }
    },
    "records": [...]
  }
}
```

**实现要点：**
- 在 `create_app()` 中初始化 `UsageManager` 并注入 `app.config`
- 复用 `_ok()` / `_err()` 响应封装
- 日期参数验证

**依赖：** Task 2

---

### Task 11: `cf init` 初始化更新

**File:** `claude_flow/cli.py`

**变更：**

1. 在 `init` 命令中将 `.claude-flow/usage.jsonl` 加入 `.gitignore`
2. 无需创建 usage.jsonl 文件（`UsageStore.append()` 首次写入时自动创建）

**影响面：** 仅修改 `cli.py:94` 处的 `ignore_lines` 列表

**依赖：** 无

---

### Task 12: conftest.py Mock 适配

**File:** `tests/conftest.py`

**变更：**

1. `ClaudeSubprocessGuard`：
   - `mock_popen()` 和 `mock_run()` 的 stream-json 输出中增加 `result` 事件的 `usage` 和 `modelUsage` 字段
   - 当前 `_task_stdout` 默认值为 `'{"type":"result","result":"done"}'`，需要扩展为包含完整 token 用量的 JSON

2. 新增 mock 数据：

```python
_task_stdout: str = (
    '{"type":"result","result":"done","session_id":"mock-session-001",'
    '"total_cost_usd":0.1234,"duration_ms":5000,'
    '"usage":{"input_tokens":1000,"output_tokens":200,'
    '"cache_creation_input_tokens":500,"cache_read_input_tokens":300},'
    '"modelUsage":{"Claude-Opus-4.6":{"inputTokens":1000,"outputTokens":200,'
    '"cacheReadInputTokens":300,"cacheCreationInputTokens":500,"costUSD":0.1234}}}'
)
```

3. 需要额外 patch `usage.run_claude` 相关模块的 subprocess 调用（如果 planner/chat 改为使用 `run_claude()`）

4. `monkeypatch` 补丁路径更新：
   - 新增 `claude_flow.usage.subprocess.Popen`
   - 可能需要更新 `claude_flow.chat.subprocess.run`
   - 可能需要更新 `claude_flow.progress.subprocess.run`

**依赖：** Task 3, Task 4, Task 5, Task 6, Task 7, Task 8

---

### Task 13: 新增 `tests/test_usage.py` 单元测试

**File:** `tests/test_usage.py` (新建)

**测试用例：**

1. **UsageRecord 测试**
   - `test_usage_record_to_dict` -- 序列化
   - `test_usage_record_from_dict` -- 反序列化
   - `test_usage_record_total_tokens` -- total_tokens 计算

2. **UsageStore 测试**
   - `test_usage_store_append_and_load` -- 追加后读取
   - `test_usage_store_empty_file` -- 空文件返回空列表
   - `test_usage_store_corrupted_line` -- 损坏行跳过
   - `test_usage_store_load_range` -- 时间范围过滤

3. **UsageManager 测试**
   - `test_get_task_usage` -- 按 task 聚合
   - `test_get_total_usage` -- 总用量
   - `test_get_daily_usage` -- 按日聚合
   - `test_get_monthly_usage` -- 按月聚合
   - `test_get_model_breakdown` -- 按模型分组

4. **extract 函数测试**
   - `test_extract_usage_from_stream_json` -- 正常提取
   - `test_extract_usage_no_result` -- 无 result 事件
   - `test_extract_text_from_stream_json` -- 文本提取
   - `test_extract_text_empty` -- 空输出

5. **run_claude 测试**
   - `test_run_claude_success` -- 正常调用并写入 usage
   - `test_run_claude_failure` -- 非零退出码
   - `test_run_claude_timeout` -- 超时

**依赖：** Task 1, 2, 3

---

### Task 14: 全量测试验证 + 回归修复

**执行内容：**

1. 运行 `pytest -v` 确认所有现有测试通过
2. 检查改造后的模块是否有遗漏（grep 确认无残留的 `--output-format text`）
3. 验证 `cf usage` 命令在有/无数据时的输出
4. 验证 Web API `/api/usage` 端点响应

**验收标准：**
- `pytest -v` 全部通过
- 所有 Claude CLI 调用点（共 9 个）都通过 `run_claude()` 或在调用后写入 usage JSONL
- `grep -r "output-format.*text" claude_flow/` 无结果（除了注释和文档）
- `cf usage` 在空数据时显示友好提示
- `cf usage` 在有数据时正确展示表格

**依赖：** 所有前置 Task

---

## 调用点清单（改造前 -> 改造后）

| # | 模块 | 方法 | 行号 | 原格式 | 改造方式 | call_type |
|---|------|------|------|--------|----------|-----------|
| 1 | `planner.py` | `generate()` | 76 | `--output-format text` | -> `run_claude()` | `plan_auto` |
| 2 | `planner.py` | `generate_from_chat()` | 148 | `--output-format text` | -> `run_claude()` | `plan_chat` |
| 3 | `chat.py` | `send_initial_prompt()` | 211 | `--output-format text` | -> `run_claude()` | `chat_init` |
| 4 | `chat.py` | `send_message()` | 265 | `--output-format text` | -> `run_claude()` | `chat_msg` |
| 5 | `chat.py` | `_async_claude_call()` | 383 | `--output-format text` | -> `run_claude()` | `chat_async` |
| 6 | `progress.py` | `log_success()` | 59 | `--output-format text` | -> `run_claude()` | `progress` |
| 7 | `worktree.py` | `rebase_and_merge()` | 197 | 无 format 参数 | -> `run_claude()` | `rebase` |
| 8 | `worker.py` | `_execute_task_inner()` | 69 | `stream-json` | 保留 `_run_streaming()`，末尾追加 usage 采集 | `task_exec` |
| 9 | `worker.py` | `_run_pre_merge_tests()` | 239 | `stream-json` | -> `run_claude()` | `test_fix` |

---

## 执行依赖图

```
Task 1 (UsageRecord + UsageStore)
  |---> Task 2 (UsageManager) ---> Task 9 (CLI cf usage)
  |                            \-> Task 10 (Web API)
  \---> Task 3 (extract + run_claude)
         |---> Task 4 (planner.py 改造)
         |---> Task 5 (chat.py 改造)
         |---> Task 6 (progress.py 改造)
         |---> Task 7 (worktree.py 改造)
         \---> Task 8 (worker.py 改造)

Task 11 (cf init 更新) --- 独立

Task 12 (conftest mock) --- 依赖 Task 4-8
Task 13 (test_usage.py) --- 依赖 Task 1-3
Task 14 (全量验证) ------- 依赖所有
```

## 推荐执行顺序

**Phase 1 -- 基础层（可并行）：**
- Task 1 + Task 11（独立）

**Phase 2 -- 核心功能（串行）：**
- Task 3 -> Task 13（边实现边测试）

**Phase 3 -- 模块改造（可并行）：**
- Task 4, 5, 6, 7, 8（各自独立）
- Task 2

**Phase 4 -- 展示层（可并行）：**
- Task 9, Task 10

**Phase 5 -- 收尾：**
- Task 12 -> Task 14

---

## 风险评估

| 风险 | 影响 | 缓解 |
|------|------|------|
| stream-json 格式在 chat 场景下输出文本提取不完整 | 聊天内容丢失 | Task 3 中编写全面的 text 提取测试，覆盖多轮对话场景 |
| `run_claude()` 的 KeyboardInterrupt 处理不一致 | 用户中断时进程挂起 | 复用 `Planner._run_claude()` 现有的中断处理逻辑 |
| Worker 的 `_run_streaming()` 实时日志与 usage 采集冲突 | 日志写入异常 | Worker 采用方案 A（在流式处理后追加采集），不修改现有流式逻辑 |
| JSONL 文件在高并发下写入竞争 | 数据丢失或损坏 | 使用 `fcntl.flock(LOCK_EX)` 排他锁保护每次追加写入 |
| 已有测试 mock 不匹配新的 subprocess 调用模式 | 测试失败 | Task 12 专门处理 mock 适配 |
