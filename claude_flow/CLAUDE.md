[根目录](../CLAUDE.md) > **claude_flow**

# claude_flow -- 核心包

## 模块职责

`claude_flow` 是项目的唯一 Python 包，实现了多实例 Claude Code 工作流管理的全部核心逻辑：CLI 命令解析、任务队列管理、计划生成与审批、Worker 执行引擎、Git Worktree 操作、配置管理，以及 Mini Task 轻量交互式任务（PTY 终端 + WebSocket + xterm.js）。

## 入口与启动

- **CLI 入口**: `cli.py` 中的 `main` 函数，通过 `pyproject.toml` 注册为 `cf` 命令
- **项目根检测**: `cli._get_root()` 沿目录树向上查找 `.claude-flow/` 或 `.git/`，或使用 `CF_PROJECT_ROOT` 环境变量

## 对外接口

### CLI 命令结构

```
cf (main)
 |-- init                     # 初始化 .claude-flow/
 |-- task (group)
 |    |-- add                  # 添加任务（-p prompt, -f 批量文件）
 |    |-- list                 # 列出所有任务
 |    |-- show <task_id>       # 查看任务详情
 |    |-- remove <task_id>     # 删除任务
 |-- plan (group, invoke_without_command=True)
 |    |-- chat <task_id>       # 交互式聊天规划（REPL 或 -m 消息）
 |    |-- finalize <task_id>   # 从聊天生成计划文档
 |    |-- review               # 交互式审批
 |    |-- approve [task_id]    # 批准计划（--all 全部）
 |-- task (group, 续)
 |    |-- mini "标题" -p "prompt"  # 创建 Mini Task（跳过 plan/review）
 |    |-- mini "标题" -p "prompt" --run  # 创建并立即启动 Mini Task
 |-- run [-n N] [task_id]      # 启动 Worker 执行
 |-- status                    # 状态总览
 |-- log <task_id>             # 查看日志
 |-- clean                     # 清理 worktree
 |-- reset <task_id>           # 重置失败任务
 |-- retry                     # 重试所有失败任务
```

### 核心类

| 类 | 文件 | 职责 |
|----|------|------|
| `Task` | `models.py` | 任务数据模型（dataclass），含序列化/反序列化 |
| `TaskStatus` | `models.py` | 任务状态枚举（9 种状态，含 INTERRUPTED） |
| `TaskType` | `models.py` | 任务类型枚举（NORMAL / MINI） |
| `Config` | `config.py` | 配置 dataclass，支持加载/保存/合并默认值 |
| `TaskManager` | `task_manager.py` | 任务 CRUD、文件锁并发安全、claim_next 领取任务 |
| `ChatSession` | `chat.py` | 聊天会话数据模型（dataclass），含消息列表和状态 |
| `ChatManager` | `chat.py` | 管理交互式计划对话，持久化 `.claude-flow/chats/` |
| `Planner` | `planner.py` | Claude Code plan mode 封装，自动/交互式生成计划文档 |
| `Worker` | `worker.py` | 单个 Worker 的执行循环：领取 -> worktree -> claude -> merge -> cleanup |
| `WorktreeManager` | `worktree.py` | Git worktree 创建/移除/合并/列表/批量清理 |
| `PtySession` | `pty_manager.py` | PTY 会话数据模型（dataclass）：task_id, pid, fd, wt_path |
| `PtyManager` | `pty_manager.py` | PTY 会话生命周期管理：创建/读写/调整大小/清理 |

## 关键依赖与配置

### 外部依赖

| 依赖 | 用途 |
|------|------|
| `click>=8.0` | CLI 框架 |
| `flask>=2.0` | Web Manager 看板应用（可选） |
| `flask-sock>=0.7` | WebSocket 支持，Mini Task 终端通信（可选） |
| `fcntl` (stdlib) | 文件锁实现并发安全 |
| `pty` (stdlib) | PTY 会话创建（Mini Task 终端） |
| `subprocess` (stdlib) | 调用 git 和 claude CLI |
| `json` (stdlib) | 任务和配置的 JSON 持久化 |

### 配置参数（Config dataclass）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_workers` | 2 | 最大并行 Worker 数 |
| `main_branch` | "main" | 主分支名称 |
| `claude_args` | [] | 传给 Claude Code 的额外参数 |
| `auto_merge` | True | 完成后自动合并 |
| `merge_strategy` | "--no-ff" | Git 合并策略 |
| `worktree_dir` | ".claude-flow/worktrees" | Worktree 存放路径 |
| `skip_permissions` | True | 使用 --dangerously-skip-permissions |
| `plan_prompt_prefix` | "请分析以下任务..." | Plan 模式的 prompt 前缀 |
| `task_prompt_prefix` | "你的任务是:" | 执行模式的 prompt 前缀 |
| `task_timeout` | 600 | 任务超时（秒） |

## 数据模型

### Task 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 自动生成 "task-{6位hex}"  |
| `title` | str | 任务标题 |
| `prompt` | str | 给 Claude Code 的 prompt |
| `status` | TaskStatus | 当前状态 |
| `task_type` | TaskType | 任务类型（NORMAL / MINI） |
| `branch` | Optional[str] | 工作分支名 "cf/{task_id}" |
| `plan_file` | Optional[str] | 计划文件路径 |
| `worker_id` | Optional[int] | 执行该任务的 Worker ID |
| `created_at` | datetime | 创建时间 |
| `started_at` | Optional[datetime] | 开始执行时间 |
| `completed_at` | Optional[datetime] | 完成时间 |
| `error` | Optional[str] | 错误信息 |

### 并发安全机制

`TaskManager._with_lock()` 使用 `fcntl.flock(LOCK_EX)` 对 `tasks.lock` 加排他锁，保证多 Worker 并发领取任务时的安全性。

## 测试与质量

- 每个源文件都有对应的测试文件（1:1 覆盖）
- 外部调用（`claude` CLI, `git` 命令）通过 `unittest.mock.patch` 隔离
- Git 操作测试使用 `conftest.py` 中的 `git_repo` fixture（临时真实 git 仓库）

## 常见问题 (FAQ)

**Q: 为什么用 `fcntl` 而不是 `threading.Lock`？**
A: 因为多 Worker 是多进程模型（multiprocessing/subprocess），`fcntl.flock` 是跨进程的文件锁。

**Q: 如何在 Windows 上运行？**
A: 当前不支持 Windows，因为 `fcntl` 是 POSIX 独有模块。

**Q: Worker 如何调用 Claude Code？**
A: 通过 `subprocess.run(["claude", "-p", prompt, ...])` 调用 Claude Code CLI，不直接使用 API。

## 相关文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `__init__.py` | 2 | 包声明，版本号 |
| `models.py` | 82 | Task dataclass、TaskStatus 枚举 |
| `chat.py` | 190 | ChatSession/ChatMessage 模型、ChatManager |
| `config.py` | 55 | Config dataclass、加载/保存/默认值 |
| `task_manager.py` | 121 | 任务 CRUD、文件锁、claim_next |
| `planner.py` | 50 | Plan 生成/读取/审批/拒绝 |
| `worker.py` | 98 | Worker 执行循环 |
| `worktree.py` | 52 | Git worktree 操作 |
| `pty_manager.py` | ~120 | PTY 会话管理（创建/读写/清理） |
| `cli.py` | 366 | Click CLI 全部命令 |

## Mini Task 架构

Mini Task 是轻量级交互式任务，跳过 plan/review 阶段，通过浏览器终端直接与 Claude CLI 交互。

### 生命周期

```
approved --> running (PTY + worktree) --> merging --> done
                                     \-> interrupted (服务器重启)
```

### 核心组件

- **PtyManager** (`pty_manager.py`): 管理 PTY 伪终端会话，每个 Mini Task 对应一个 PTY 进程
- **WebSocket 桥** (`web/ws.py`): 通过 flask-sock 将浏览器 xterm.js 与服务器 PTY 双向连接
- **REST API** (`web/api.py`): 7 个 Mini Task 端点（创建/启动/停止/diff/合并/丢弃/列表）
- **前端 UI** (`web/templates/index.html`): 侧边栏 Mini Task 列表 + xterm.js 终端 + Diff 预览
- **恢复机制** (`web/app.py`): 服务器重启时将 RUNNING 状态的 Mini Task 标记为 INTERRUPTED

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/mini-tasks` | 列出所有 Mini Task |
| POST | `/api/mini-tasks` | 创建新 Mini Task |
| POST | `/api/mini-tasks/<id>/start` | 启动 PTY + worktree |
| POST | `/api/mini-tasks/<id>/stop` | 停止 PTY，自动提交 |
| GET | `/api/mini-tasks/<id>/diff` | 查看变更 diff |
| POST | `/api/mini-tasks/<id>/merge` | 合并到主分支 |
| POST | `/api/mini-tasks/<id>/discard` | 丢弃变更 |

### WebSocket

| 路径 | 说明 |
|------|------|
| `/ws/terminal/<task_id>` | xterm.js 双向终端通信 |

## 变更记录 (Changelog)

| 时间 | 操作 |
|------|------|
| 2026-03-10 | 添加 Mini Task 功能：PtyManager、WebSocket、REST API、前端 UI、INTERRUPTED 状态 |
| 2026-03-05T14:07:01 | 初始化模块文档（init-architect 自适应扫描） |
