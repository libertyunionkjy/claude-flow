# Token Usage Statistics Design

> Date: 2026-03-06
> Status: Draft
> Author: AI-assisted

## 1. 目标

为 Claude Flow 添加 token 使用量统计功能，提供与 `ccusage` 一致的详尽统计视图，包括：

- 按 **session**（即 task）维度的详细用量和费用
- 按 **日** 聚合统计
- 按 **月** 聚合统计
- 支持 CLI（`cf usage`）和 Web 两种展示方式

## 2. 方案：集成 ccusage

### 2.1 为什么选择 ccusage

| 因素 | 说明 |
|------|------|
| **数据已就位** | Claude Code 的 `claude -p` 调用会自动将 session 数据写入 `~/.claude/projects/` 下，每个 worktree 一个独立目录 |
| **ccusage 完美适配** | ccusage 正是从 `~/.claude/projects/*.jsonl` 解析 token 用量，支持 `daily`、`monthly`、`session` 等多种报告 |
| **零重复开发** | 无需自建 JSONL 解析、价格计算（LiteLLM pricing）、表格渲染等逻辑 |
| **持续维护** | ccusage 社区活跃，自动跟进 Claude Code 格式变更和新模型价格 |

### 2.2 ccusage 核心能力

```
ccusage daily    [--since DATE] [--until DATE] [--project PATTERN] [--json]
ccusage monthly  [--since DATE] [--until DATE] [--project PATTERN] [--json]
ccusage session  [--since DATE] [--until DATE] [--project PATTERN] [--json]
ccusage blocks   [--active] [--recent] [--token-limit N]
```

**关键选项：**

- `--project PATTERN`：按项目路径过滤（支持子字符串匹配）
- `--json`：输出结构化 JSON，可被程序化解析
- `--since / --until`：日期范围过滤
- `--timezone / --locale`：时区和本地化
- `--compact`：紧凑表格模式
- `--mode`：费用计算模式（auto/calculate/display）

**安装方式：**

```bash
npx ccusage@latest           # 推荐，始终最新版
npm install -g ccusage        # 全局安装
```

### 2.3 Session 目录映射规则

Claude Code 将项目 `cwd` 路径中的 `/` 替换为 `-` 作为 `~/.claude/projects/` 下的目录名：

```
项目根:     /opt/shared/claude-flow
Session 目录: ~/.claude/projects/-opt-shared-claude-flow/

Worktree:   /opt/shared/claude-flow/.claude-flow/worktrees/task-759ee1
Session 目录: ~/.claude/projects/-opt-shared-claude-flow--claude-flow-worktrees-task-759ee1/
```

**关键发现：** 所有 worktree 的 session 目录都以项目根路径的编码为前缀。因此使用 `--project` 过滤时，传入项目根路径即可匹配所有关联 session（主项目 + 所有 worktree task）。

### 2.4 JSONL 数据格式

每个 session 的 `.jsonl` 文件包含 `assistant` 类型条目，其中嵌入 `usage` 字段：

```json
{
  "type": "assistant",
  "message": {
    "model": "claude-opus-4-6",
    "usage": {
      "input_tokens": 3677,
      "cache_creation_input_tokens": 58687,
      "cache_read_input_tokens": 785931,
      "output_tokens": 6376
    }
  }
}
```

而 `--output-format stream-json` 的 `result` 事件（存储在 `.claude-flow/logs/` 中）包含汇总数据：

```json
{
  "type": "result",
  "total_cost_usd": 0.9375,
  "usage": {
    "input_tokens": 3677,
    "cache_creation_input_tokens": 58687,
    "cache_read_input_tokens": 785931,
    "output_tokens": 6376
  },
  "modelUsage": {
    "Claude-Opus-4.6": {
      "inputTokens": 3677,
      "outputTokens": 6376,
      "cacheReadInputTokens": 785931,
      "cacheCreationInputTokens": 58687,
      "costUSD": 0.9375
    }
  }
}
```

## 3. 架构设计

### 3.1 组件结构

```
cf usage [subcommand]
    |
    +-- UsageManager (claude_flow/usage.py)
    |     |-- _check_ccusage()        # 检测 ccusage 是否可用
    |     |-- _get_project_filter()   # 生成 --project 过滤参数
    |     |-- _run_ccusage()          # 调用 ccusage CLI
    |     |-- _parse_json_output()    # 解析 --json 输出
    |     |-- _enrich_with_tasks()    # 将 session 与 task 关联
    |     +-- _fallback_from_logs()   # ccusage 不可用时的降级方案
    |
    +-- CLI (cli.py 新增 usage group)
          |-- cf usage               # 默认：session 报告（按 task）
          |-- cf usage daily         # 日统计
          |-- cf usage monthly       # 月统计
          +-- cf usage summary       # 总览（总花费、总 token）
```

### 3.2 数据流

```
┌─────────────────────────────────────────────────────────┐
│  ~/.claude/projects/-{project-path}*/                   │
│    ├── {session-id}.jsonl  (Claude Code 自动写入)        │
│    └── ...                                              │
└──────────────────────┬──────────────────────────────────┘
                       │ ccusage --project <path> --json
                       v
┌─────────────────────────────────────────────────────────┐
│  UsageManager._run_ccusage()                            │
│    解析 JSON 输出，按 session/日/月 返回结构化数据         │
└──────────────────────┬──────────────────────────────────┘
                       │
                       v
┌─────────────────────────────────────────────────────────┐
│  UsageManager._enrich_with_tasks()                      │
│    将 session ID 与 task ID 关联                         │
│    （通过日志文件中的 session_id 字段匹配）                │
└──────────────────────┬──────────────────────────────────┘
                       │
                       v
┌─────────────────────────────────────────────────────────┐
│  CLI / Web 展示                                         │
│  ┌─ cf usage ─────────────────────────────────────────┐ │
│  │ Task        Models          Input   Output  ...    │ │
│  │ task-759ee1  Claude-Opus-4.6  3.6K    6.3K  $0.94  │ │
│  │ task-c08052  Claude-Opus-4.6  5.1K    8.2K  $1.23  │ │
│  │ ───────────────────────────────────────────── ──── │ │
│  │ Total                         8.7K   14.5K  $2.17  │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 3.3 Task-Session 关联

关键设计点：ccusage 输出按 session 组织，但用户关心的是按 task 统计。关联方法：

1. **主要方法**：从 `.claude-flow/logs/{task_id}.log` 中提取 `session_id` 字段
2. **辅助方法**：session 目录名包含 `task-{id}`（如 `-opt-shared-claude-flow--claude-flow-worktrees-task-759ee1`），可通过目录名反推 task ID

```python
# 从 stream-json 日志中提取 session_id
def _build_task_session_map(self) -> dict[str, str]:
    """返回 {session_id: task_id} 映射。"""
    mapping = {}
    for log_file in self._logs_dir.glob("task-*.log"):
        task_id = log_file.stem  # e.g., "task-759ee1"
        for line in reversed(log_file.read_text().splitlines()):
            try:
                obj = json.loads(line)
                if obj.get("type") == "result" and obj.get("session_id"):
                    mapping[obj["session_id"]] = task_id
                    break
            except (json.JSONDecodeError, KeyError):
                continue
    return mapping
```

### 3.4 降级方案（ccusage 不可用时）

当 `npx` 或 `ccusage` 不可用时，从自有的 stream-json 日志文件（`.claude-flow/logs/task-*.log`）中提取 `result` 事件的 `usage` 和 `total_cost_usd` 字段。

**限制：**
- 仅能提供 task 维度统计（无日/月聚合的时间粒度）
- 不包含 planner 阶段的 Claude 调用（plan 阶段未存 stream-json 日志）
- 价格为 Claude Code 自报值，无法使用最新的 LiteLLM 定价

## 4. CLI 设计

### 4.1 命令接口

```bash
# 默认：按 task/session 展示（最常用）
cf usage

# 按日统计
cf usage daily [--since 2026-03-01] [--until 2026-03-06]

# 按月统计
cf usage monthly

# 总览摘要
cf usage summary

# 直接透传 ccusage 参数
cf usage raw [-- <ccusage-args>]
```

### 4.2 输出格式

**`cf usage`（默认 session/task 视图）：**

```
Claude Flow Usage Report
========================

Task         │ Session     │ Models          │    Input │   Output │ Cache Create │ Cache Read │ Total Tokens │ Cost (USD)
─────────────┼─────────────┼─────────────────┼──────────┼──────────┼──────────────┼────────────┼──────────────┼───────────
task-759ee1  │ 72001b15... │ Claude-Opus-4.6 │    3,677 │    6,376 │       58,687 │    785,931 │      854,671 │     $0.938
task-c08052  │ a3b0c71e... │ Claude-Opus-4.6 │    5,102 │    8,244 │       42,100 │    612,000 │      667,446 │     $1.231
task-fcf3e7  │ 8e95a546... │ Claude-Opus-4.6 │    2,891 │    4,512 │       31,200 │    420,000 │      458,603 │     $0.672
─────────────┼─────────────┼─────────────────┼──────────┼──────────┼──────────────┼────────────┼──────────────┼───────────
Total (3)    │             │                 │   11,670 │   19,132 │      131,987 │  1,817,931 │    1,980,720 │     $2.841
```

**`cf usage daily`：**

```
Daily Usage Report
==================

Date        │    Input │   Output │ Cache Create │ Cache Read │ Total Tokens │ Cost (USD)
────────────┼──────────┼──────────┼──────────────┼────────────┼──────────────┼───────────
2026-03-05  │    8,779 │   14,620 │       89,787 │  1,397,931 │    1,511,117 │     $2.169
2026-03-06  │    2,891 │    4,512 │       42,200 │    420,000 │      469,603 │     $0.672
────────────┼──────────┼──────────┼──────────────┼────────────┼──────────────┼───────────
Total       │   11,670 │   19,132 │      131,987 │  1,817,931 │    1,980,720 │     $2.841
```

### 4.3 Web 集成

在现有 Web 管理界面中添加 Usage 标签页：

- 调用后端 API `/api/usage?type=session|daily|monthly`
- 后端通过 `UsageManager` 获取数据，返回 JSON
- 前端用表格渲染，支持排序和筛选

## 5. 实现计划

### Phase 1：核心功能（MVP）

| 步骤 | 内容 | 估计代码量 |
|------|------|------------|
| 1 | 新建 `claude_flow/usage.py`，实现 `UsageManager` 类 | ~120 行 |
| 2 | CLI 添加 `cf usage` 命令组（`cli.py`） | ~80 行 |
| 3 | 实现 ccusage 调用与 JSON 输出解析 | 包含在步骤 1 |
| 4 | 实现 task-session 关联逻辑 | ~40 行 |
| 5 | 实现降级方案（从自有日志解析） | ~60 行 |
| 6 | 单元测试 `tests/test_usage.py` | ~100 行 |

**总计：约 400 行代码**

### Phase 2：Web 集成

| 步骤 | 内容 |
|------|------|
| 7 | 后端 API endpoint `/api/usage` |
| 8 | 前端 Usage 标签页渲染 |

### Phase 3：增强功能（可选）

| 步骤 | 内容 |
|------|------|
| 9 | 本地缓存 ccusage 结果（避免重复解析） |
| 10 | `cf usage` 支持 `--format json` 输出 |
| 11 | 费用预警（单任务费用超阈值时提醒） |

## 6. 依赖与前置条件

| 依赖 | 必需性 | 说明 |
|------|--------|------|
| Node.js + npx | **可选** | ccusage 运行需要；不可用时自动降级 |
| ccusage | **可选** | 通过 `npx ccusage@latest` 调用，无需全局安装 |
| `~/.claude/projects/` | **必需** | Claude Code 的 session 数据目录，由 CC 自动管理 |

## 7. 风险与应对

| 风险 | 概率 | 应对 |
|------|------|------|
| ccusage 未安装且 npx 不可用 | 中 | 降级到自有日志解析，功能受限但可用 |
| ccusage `--project` 匹配过于宽泛 | 低 | 使用完整项目路径作为过滤条件 |
| ccusage JSON 输出格式变化 | 低 | 版本检测 + 解析容错 |
| session 目录路径编码规则变化 | 极低 | 这是 Claude Code 内部稳定机制 |
| planner 阶段的调用无法统计 | 中 | Phase 1 暂不处理；后续可在 planner 调用中也记录 session_id |

## 8. 决策记录

- **选择 ccusage 而非自建**：避免重复实现 JSONL 解析、价格查询、多模型支持等复杂逻辑
- **ccusage 作为可选依赖**：保持 Claude Flow 的 Python-only 安装体验，ccusage 不可用时降级
- **npx 调用而非全局安装**：降低安装门槛，确保始终使用最新版本
- **通过 `--json` 程序化集成**：避免解析 ccusage 的终端表格输出，稳定可靠
