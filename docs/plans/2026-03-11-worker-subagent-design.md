# Worker Subagent 模式设计

## 概述

为 claude-flow 的 Worker 执行层添加 subagent 支持，让用户可以选择在任务执行时启用 Claude Code 的 Task tool 子代理能力，将复杂任务拆分为多个子任务并行处理。

## 背景

当前 Worker 通过 `subprocess.Popen` 调用 `claude -p prompt` 以单进程方式执行任务。对于复杂任务（如大规模重构、多文件特性开发），单一 Claude 实例可能受限于上下文窗口和串行执行效率。

Claude Code 内置的 Task tool（subagent）可以：
- 将任务拆分为独立子任务并行执行
- 每个 subagent 有独立上下文窗口
- 支持多种专业化 agent 类型（Explore、Plan、code-reviewer 等）

## 设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 控制粒度 | 全局配置 + 任务级覆盖 | 灵活性与简洁性平衡 |
| 注入方式 | Prompt 前缀扩展 | 改动最小，无副作用，与现有架构一致 |
| 策略控制 | 简单开关 | KISS，避免过早暴露复杂配置 |
| 任务类型 | 仅 NORMAL 任务 | Mini Task 是交互式 PTY，用户可自行调度 |

## 详细设计

### 1. 数据模型变更

**`models.py` — Task**

新增字段：

```python
use_subagent: Optional[bool] = None  # None = 继承全局配置
```

`to_dict()` / `from_dict()` 同步更新序列化逻辑。

**`config.py` — Config**

新增字段：

```python
use_subagent: bool = False  # 全局默认关闭
```

`DEFAULT_CONFIG` 同步更新。

**生效优先级：**

```
task.use_subagent（非 None 时）> config.use_subagent
```

### 2. Worker Prompt 构建

**`worker.py`** — 提取 `_build_prompt(task)` 方法：

```python
SUBAGENT_PROMPT = """
当你面对此任务时，请考虑将其拆分为多个独立子任务并行处理。
使用 Task tool 启动 subagent 来并行执行这些子任务。
每个 subagent 应该有明确的职责边界，独立完成后汇总结果。
优先使用 general-purpose 类型的 subagent。
如果子任务之间有依赖关系，按依赖顺序串行执行。
如果任务足够简单不需要拆分，直接执行即可。
"""

def _build_prompt(self, task: Task) -> str:
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

设计要点：
- **不强制拆分** — "请考虑" 而非 "必须"，让 Claude 自行判断任务复杂度
- **给出方法论** — 明确指出使用 Task tool 和 subagent 类型
- **容错** — 简单任务允许直接执行，避免过度拆分

当前 `_execute_task_simple()` 和 `_execute_task_git()` 中的 prompt 拼接逻辑统一使用 `_build_prompt()`。

### 3. CLI 接口

**`cli.py` — `task add` 命令**

新增选项：

```python
@click.option("--subagent/--no-subagent", default=None,
              help="是否使用 subagent 模式（默认继承全局配置）")
```

用法：

```bash
# 继承全局配置（默认 false）
cf task add -p "实现用户登录" "登录功能"

# 显式启用
cf task add -p "重构整个认证系统" "认证重构" --subagent

# 显式关闭
cf task add -p "修复小 bug" "修复" --no-subagent
```

**`cf task list`** 输出中对启用 subagent 的任务显示 `[S]` 标记。

### 4. 影响范围

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `models.py` | 修改 | Task 新增 `use_subagent` 字段 + 序列化 |
| `config.py` | 修改 | Config 新增 `use_subagent` + DEFAULT_CONFIG |
| `worker.py` | 修改 | 提取 `_build_prompt()`，注入 subagent prompt |
| `cli.py` | 修改 | `task add` 新增 `--subagent` 选项 |
| `test_models.py` | 修改 | 新字段序列化测试 |
| `test_worker.py` | 修改 | subagent prompt 注入测试 |
| `test_cli.py` | 修改 | 新 CLI 选项测试 |

**不受影响：** `planner.py`、`worktree.py`、`task_manager.py`、`pty_manager.py`、Mini Task 相关模块。

## 向后兼容性

- `use_subagent` 默认为 `False`（Config）/ `None`（Task），现有行为不变
- `tasks.json` 中缺少 `use_subagent` 字段的旧任务通过 `from_dict` 的 `d.get()` 兼容
- 无需数据迁移
