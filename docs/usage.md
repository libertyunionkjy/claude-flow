# Claude Flow 使用文档

## 安装与环境

### 前置要求

- Python 3.10+
- Git（支持 worktree）
- Claude Code CLI（已安装并可通过 `claude` 命令调用）
- Linux 或 macOS（文件锁依赖 `fcntl`）

### 安装

```bash
git clone <repo-url> && cd claude-flow
pip install -e .

# Web 看板功能（可选）
pip install flask
```

验证安装：

```bash
cf --help
```

---

## 初始化

在目标项目根目录下运行：

```bash
cd /path/to/your-project
cf init
```

该命令会创建以下目录结构：

```
your-project/
└── .claude-flow/
    ├── config.json       # 配置文件
    ├── tasks.json        # 任务队列（自动生成）
    ├── tasks.lock        # 文件锁（自动生成）
    ├── logs/             # 执行日志
    ├── plans/            # 生成的计划文件
    └── worktrees/        # 工作树（执行时创建，完成后清理）
```

同时自动将临时文件添加到 `.gitignore`。

---

## 任务管理

### 添加单个任务

```bash
# 通过 -p 直接指定 prompt
cf task add -p "实现 RESTful 用户注册 API，包含邮箱验证和密码加密" "用户注册"

# 带优先级添加（数字越大越优先）
cf task add -p "紧急修复登录 bug" -P 10 "紧急修复"

# 不带 -p 会打开编辑器编写 prompt
cf task add "数据库迁移"
```

### 批量导入

准备文件 `tasks.txt`，每行格式为 `标题 | prompt`：

```
用户登录 | 实现 JWT 登录接口，支持邮箱和手机号
用户注册 | 实现注册接口，包含邮箱验证
密码重置 | 实现密码重置流程，发送重置链接到邮箱
```

导入：

```bash
cf task add -f tasks.txt "批量导入"
```

> 注意：使用 `-f` 时 title 参数会被忽略，标题从文件中读取。

### 查看任务

```bash
# 列表视图（按优先级降序排列）
cf task list
#   ○ task-a1b2c3  pending    P10  紧急修复
#   ○ task-d4e5f6  pending     P5  用户登录
#   ✓ task-789abc  approved         用户注册
#   ▶ task-def012  running          密码重置

# 详情视图
cf task show task-a1b2c3
```

状态图标含义：

| 图标 | 状态 | 说明 |
|------|------|------|
| `○` | pending | 等待处理 |
| `⟳` | planning | 正在生成计划 |
| `◉` | planned | 计划已生成，等待审批 |
| `✓` | approved | 已审批，等待执行 |
| `▶` | running | 正在执行 |
| `⇄` | merging | 正在合并 |
| `●` | done | 已完成 |
| `✗` | failed | 执行失败 |

### 删除任务

```bash
cf task remove task-a1b2c3
```

---

## Plan Mode 工作流

Plan mode 让 Claude Code 先生成实施计划，人工审核后再执行，适合需要质量把控的场景。

### 生成计划

```bash
# 为所有 pending 任务生成计划
cf plan

# 为指定任务生成计划
cf plan -t task-a1b2c3
```

计划文件保存在 `.claude-flow/plans/task-xxx.md`，采用结构化格式（YAML front matter + Markdown body）。

### 审批计划

**交互式审批：**

```bash
cf plan review
```

每个计划会展示内容并提供操作选项：

| 按键 | 行为 |
|------|------|
| `a` | 审批通过，状态变为 approved |
| `r` | 拒绝，输入原因后状态回到 pending（原因追加到 prompt） |
| `s` | 跳过当前任务 |
| `e` | 用编辑器打开计划文件，编辑后自动审批 |
| `f` | **多轮反馈**：输入反馈意见，Claude 基于反馈重新生成计划 |
| `q` | 退出审批 |

**快速审批：**

```bash
# 审批指定任务
cf plan approve task-a1b2c3

# 审批所有已生成计划的任务
cf plan approve --all
```

### 计划版本管理

每次通过 `[f]eedback` 重新生成计划时，之前的版本会自动保存为 `task-xxx_v1.md`、`task-xxx_v2.md`，最新版本始终在 `task-xxx.md`。

---

## 执行任务

### 单 Worker 执行

```bash
# 自动领取并执行所有 approved 任务（按优先级顺序）
cf run

# 执行指定任务
cf run task-a1b2c3
```

### 多 Worker 并行执行

```bash
# 3 个 Worker 并行
cf run -n 3
```

### 守护进程模式

```bash
# 持续轮询，干完一个活自动接下一个（Ctrl+C 优雅停止）
cf run --daemon

# 多 Worker 守护进程
cf run -n 3 --daemon
```

守护进程模式下，Worker 会在没有任务时每隔 `daemon_poll_interval` 秒（默认 10 秒）检查一次新任务，直到收到 SIGINT/SIGTERM 信号。

### Worker 执行流程

每个 Worker 的完整执行流程：

1. 通过文件锁从任务队列领取一个 approved 任务（按优先级降序）
2. 创建独立的 git worktree：`.claude-flow/worktrees/task-xxx/`
3. 设置 symlink 共享文件（如配置了 `shared_symlinks`）
4. 在 worktree 中运行 Claude Code（端口 = `base_port + worker_id`）
5. **合并前测试验证**（如配置了 `pre_merge_commands`）
   - 测试失败时调用 Claude 修复，最多重试 `max_test_retries` 次
6. **Rebase 合并**到主分支（支持冲突自动解决，最多重试 `max_merge_retries` 次）
7. **远程推送**（如配置了 `auto_push`）
8. **记录经验**到 PROGRESS.md（如启用了 `enable_progress_log`）
9. 清理 worktree，标记任务为 done
10. 循环领取下一个任务

---

## 监控

### 实时监控

```bash
# 实时查看 worker 活动（每 2 秒刷新）
cf watch

# 自定义刷新间隔
cf watch --interval 5
```

显示每个 Worker 的当前任务、事件数、工具调用数、错误数和最近活动。

### 状态总览

```bash
cf status
# Total tasks: 5
#   approved: 1
#   done: 3
#   failed: 1
#
# Active workers:
#   Worker-0: task=task-a1b2c3 events=42
```

### 执行日志

```bash
cf log task-a1b2c3
```

### 经验沉淀

```bash
# 查看 PROGRESS.md 经验日志
cf progress
```

PROGRESS.md 记录每次任务完成/失败后的经验教训，包含 commit ID、错误信息和 Claude 生成的经验总结。

---

## Web Manager 看板

启动 Web 看板界面（需安装 Flask）：

```bash
# 默认端口 8080
cf web

# 指定端口
cf web --port 3000
```

看板功能：
- **7 列看板布局**：Pending / Planning / Planned / Approved / Running / Done / Failed
- **任务卡片**：显示 ID、标题、优先级徽章、Worker ID
- **点击展开详情**：查看 prompt、错误信息、时间戳
- **新建任务表单**：直接在 Web 界面创建任务
- **审批操作**：Planned 状态可直接 Approve / Reject
- **失败处理**：Failed 状态可 Retry / Delete
- **自动刷新**：每 5 秒轮询更新
- **暗色主题**：护眼设计，响应式布局支持手机端

### REST API

Web Manager 提供以下 API 端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tasks` | 任务列表（支持 `?status=` 筛选） |
| `POST` | `/api/tasks` | 创建任务（body: `{title, prompt, priority}`） |
| `GET` | `/api/tasks/<id>` | 任务详情 |
| `PATCH` | `/api/tasks/<id>` | 更新任务（状态/优先级） |
| `DELETE` | `/api/tasks/<id>` | 删除任务 |
| `POST` | `/api/tasks/<id>/approve` | 审批任务 |
| `POST` | `/api/tasks/<id>/reject` | 拒绝任务（body: `{reason}`） |
| `GET` | `/api/status` | 全局状态概览 |
| `GET` | `/api/workers` | Worker 状态 |

响应格式统一为 `{"ok": true, "data": ...}` 或 `{"ok": false, "error": "..."}`。

---

## 故障处理

### 重置失败任务

```bash
# 重置单个任务为 pending
cf reset task-a1b2c3

# 重试所有失败任务（failed → approved）
cf retry
```

### 清理残留

```bash
# 清理所有 worktree 和临时分支
cf clean
```

---

## 配置

编辑 `.claude-flow/config.json`：

```jsonc
{
  // 基础配置
  "max_workers": 2,                // 最大并行 Worker 数
  "main_branch": "main",           // 主分支名称
  "claude_args": [],                // 传递给 Claude Code 的额外参数
  "skip_permissions": true,         // 使用 --dangerously-skip-permissions
  "task_timeout": 600,              // 任务超时时间（秒）
  "plan_prompt_prefix": "请分析以下任务并输出实施计划，不要执行代码:",
  "task_prompt_prefix": "你的任务是:",

  // Worktree Symlink 共享
  "shared_symlinks": ["dev-tasks.json", "api-key.json"],  // 共享文件列表
  "forbidden_symlinks": ["PROGRESS.md"],                   // 禁止 symlink 的文件

  // 合并策略
  "auto_merge": true,              // 任务完成后是否自动合并
  "merge_mode": "rebase",          // 合并模式：rebase（默认）或 merge
  "merge_strategy": "--no-ff",     // merge 模式下的策略
  "max_merge_retries": 5,          // rebase 冲突最大重试次数

  // 合并前测试
  "pre_merge_commands": ["pytest -v"],  // 合并前执行的测试命令
  "max_test_retries": 3,           // 测试失败最大重试次数

  // 远程推送
  "auto_push": false,              // 合并后是否自动推送到远程

  // PROGRESS.md 经验沉淀
  "enable_progress_log": true,     // 是否启用经验记录
  "progress_file": "PROGRESS.md",  // 经验日志文件名

  // Worker 端口分配
  "base_port": 5200,               // 端口基数（Worker-0 = 5200, Worker-1 = 5201, ...）

  // 守护进程模式
  "daemon_poll_interval": 10,      // 无任务时轮询间隔（秒）

  // Web Manager
  "web_port": 8080                 // Web 看板默认端口
}
```

### 常用配置场景

**保守模式**（禁用自动合并，手动检查每个任务的产出）：

```json
{
  "auto_merge": false,
  "max_workers": 1,
  "auto_push": false
}
```

**高并发模式**（适合大量独立任务）：

```json
{
  "max_workers": 4,
  "auto_merge": true,
  "merge_mode": "rebase",
  "auto_push": true,
  "task_timeout": 1200,
  "daemon_poll_interval": 5
}
```

**带测试验证的生产模式**：

```json
{
  "max_workers": 2,
  "merge_mode": "rebase",
  "pre_merge_commands": ["pytest -v", "npm run lint"],
  "max_test_retries": 3,
  "max_merge_retries": 5,
  "auto_push": true,
  "enable_progress_log": true,
  "shared_symlinks": [".env", "dev-tasks.json"]
}
```

**Symlink 共享配置**（多 Worker 共享开发配置文件）：

```json
{
  "shared_symlinks": ["dev-tasks.json", "api-key.json", ".env.local"],
  "forbidden_symlinks": ["PROGRESS.md"]
}
```

### 环境变量

| 变量名 | 用途 |
|--------|------|
| `CF_PROJECT_ROOT` | 覆盖自动检测的项目根目录 |
| `EDITOR` | `cf plan review` 编辑模式使用的编辑器（默认 `vi`） |
| `PORT` | Worker 执行时自动设置，值为 `base_port + worker_id` |
| `WORKER_ID` | Worker 执行时自动设置，当前 Worker 编号 |

---

## 完整使用示例

```bash
# 初始化
cd my-web-app
cf init

# 添加任务（带优先级）
cf task add -p "实现 GET /api/users 接口，返回分页用户列表" -P 5 "用户列表 API"
cf task add -p "实现 POST /api/users 接口，包含输入校验" -P 5 "创建用户 API"
cf task add -p "为用户 API 编写 pytest 测试，覆盖正常和异常路径" -P 3 "用户 API 测试"

# 生成并审批计划
cf plan
cf plan review    # 使用 [f] 提供反馈，[a] 审批

# 并行执行（守护进程模式）
cf run -n 2 --daemon

# 在另一个终端监控
cf watch          # 实时监控
cf web            # 或打开 Web 看板

# 检查结果
cf status
cf log task-xxx
cf progress       # 查看经验沉淀

# 处理失败任务
cf retry
cf run
```

---

## 注意事项

1. **Git 仓库要求**：项目必须是 Git 仓库，且至少有一次 commit
2. **合并冲突**：使用 rebase 模式时，Claude 会自动尝试解决冲突（最多 5 次）；merge 模式下冲突任务直接标记 failed
3. **Claude Code**：需要确保 `claude` 命令可用且已配置 API key
4. **文件锁**：使用 `fcntl.flock` 实现，仅支持 Linux/macOS
5. **Web Manager**：需额外安装 Flask（`pip install flask`）
6. **端口分配**：每个 Worker 分配专属端口（base_port + worker_id），通过 `PORT` 环境变量传递
7. **PROGRESS.md**：经验日志直接写入主仓库（非 worktree），使用 `git -C` 操作
