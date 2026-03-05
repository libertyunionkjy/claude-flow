# Claude Flow - 多实例 Claude Code 工作流系统设计

> 日期: 2026-03-05
> 状态: 已批准

## 概述

通用 CLI 工具，用于在任意 git 项目中管理多个 Claude Code 实例并行开发。核心能力：

1. **任务队列**（Ralph Loop）：维护任务列表，Worker 自动领取执行
2. **Git Worktree 并行化**：每个 Worker 在独立 worktree 中工作，互不干扰
3. **Plan Mode 封装**：批量生成 plan，统一 review 后再执行

## 技术选型

- 语言：Python 3.8+
- CLI 框架：Click
- 并发模型：subprocess（多进程 Worker）
- 文件锁：fcntl.flock
- 打包：pyproject.toml + pip install

## 项目结构

```
claude-flow/
├── pyproject.toml
├── claude_flow/
│   ├── __init__.py
│   ├── cli.py                # Click CLI 入口
│   ├── config.py             # 配置加载
│   ├── models.py             # 数据模型
│   ├── task_manager.py       # 任务队列 CRUD + 文件锁
│   ├── worker.py             # Worker 生命周期管理
│   ├── worktree.py           # Git worktree 操作
│   └── planner.py            # Plan mode 封装
```

## 数据模型

### TaskStatus

```python
class TaskStatus(Enum):
    PENDING = "pending"
    PLANNING = "planning"
    PLANNED = "planned"
    APPROVED = "approved"
    RUNNING = "running"
    MERGING = "merging"
    DONE = "done"
    FAILED = "failed"
```

### Task

```python
class Task:
    id: str                       # "task-001"
    title: str
    prompt: str
    status: TaskStatus
    branch: str | None            # "cf/task-001"
    plan_file: str | None
    worker_id: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
```

### 生命周期

```
pending → planning → planned → (人工 review) → approved → running → merging → done
                                                                          ↘ failed
```

## 运行时目录结构

```
your-project/
├── .claude-flow/
│   ├── tasks.json
│   ├── tasks.lock
│   ├── config.json
│   ├── logs/
│   │   └── task-001.log
│   ├── plans/
│   │   └── task-001.md
│   └── worktrees/
│       └── task-001/           # 执行中存在，完成后清理
```

## CLI 命令

### 初始化

```bash
cf init                              # 创建 .claude-flow/ 目录
```

### 任务管理

```bash
cf task add "标题"                    # 交互式输入 prompt
cf task add -p "prompt" "标题"        # 直接指定 prompt
cf task add -f tasks.txt              # 批量导入（每行: 标题 | prompt）
cf task list                          # 查看所有任务
cf task show task-001                 # 查看详情
cf task remove task-001               # 删除
```

### Plan Mode

```bash
cf plan                              # 批量生成 plan
cf plan task-001                     # 指定任务生成 plan
cf plan review                       # 交互式 review
cf plan approve task-001             # 批准
cf plan approve --all                # 全部批准
```

### 执行

```bash
cf run                               # 启动 worker
cf run -n 2                          # 2 个并行 worker
cf run task-001                      # 执行指定任务
```

### 状态与维护

```bash
cf status                            # 总览
cf log task-001                      # 查看日志
cf clean                             # 清理 worktree + 已合并分支
cf reset task-001                    # 失败任务重置为 pending
cf retry                             # 所有 failed → approved 重跑
```

## Worker 执行流程

1. 通过文件锁从 tasks.json 领取一个 approved 任务
2. `git worktree add .claude-flow/worktrees/task-xxx -b cf/task-xxx`
3. 在 worktree 中执行 `claude -p [prompt] --dangerously-skip-permissions --output-format stream-json --verbose`
4. 成功 → `git checkout main && git merge --no-ff cf/task-xxx` → 清理 worktree → 标记 done
5. 失败 → 标记 failed，日志保留
6. 合并冲突 → `git merge --abort` → 标记 failed + CONFLICT
7. 循环领取下一个任务，直到无任务

### 文件锁

```python
with open(".claude-flow/tasks.lock", "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    tasks = load_tasks()
    task = find_first_approved(tasks)
    task.status = "running"
    task.worker_id = worker_id
    save_tasks(tasks)
    fcntl.flock(lock, fcntl.LOCK_UN)
```

## Plan Mode 流程

### 生成

对每个 pending 任务调用：
```bash
claude -p "{plan_prompt_prefix} {task.prompt}" --permission-mode plan --print --output-format text
```
输出写入 `.claude-flow/plans/task-xxx.md`，状态 → planned

### Review 交互

| 按键 | 行为 |
|------|------|
| `a` | approve → 状态 approved |
| `r` | reject → 原因追加到 prompt，状态回 pending |
| `s` | skip |
| `e` | $EDITOR 编辑 plan，保存后 approve |
| `q` | 退出 |

## 配置

`.claude-flow/config.json`：

```json
{
  "max_workers": 2,
  "main_branch": "main",
  "claude_args": [],
  "auto_merge": true,
  "merge_strategy": "--no-ff",
  "worktree_dir": ".claude-flow/worktrees",
  "skip_permissions": true,
  "plan_prompt_prefix": "请分析以下任务并输出实施计划，不要执行代码:",
  "task_prompt_prefix": "你的任务是:",
  "task_timeout": 600
}
```

## 错误处理

| 场景 | 处理 |
|------|------|
| Claude Code 超时 | kill 进程，标记 failed |
| 非零退出 | 标记 failed，日志保留 |
| Merge 冲突 | merge --abort，标记 failed + CONFLICT |
| Worktree 残留 | `cf clean` 清理 |
| 文件锁死锁 | stale 超时 60s 自动释放 |
