# Claude Flow 使用文档

## 安装与环境

### 前置要求

- Python 3.10+
- Git（支持 worktree）
- Claude Code CLI（已安装并可通过 `claude` 命令调用）

### 安装

```bash
git clone <repo-url> && cd claude-flow
pip install -e .
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
# 列表视图
cf task list
#   ○ task-a1b2c3  pending     用户登录
#   ✓ task-d4e5f6  approved    用户注册
#   ▶ task-789abc  running     密码重置

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
cf plan task-a1b2c3
```

计划文件保存在 `.claude-flow/plans/task-xxx.md`。

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
| `q` | 退出审批 |

**快速审批：**

```bash
# 审批指定任务
cf plan approve task-a1b2c3

# 审批所有已生成计划的任务
cf plan approve --all
```

---

## 执行任务

### 单 Worker 执行

```bash
# 自动领取并执行所有 approved 任务
cf run

# 执行指定任务
cf run task-a1b2c3
```

### 多 Worker 并行执行

```bash
# 3 个 Worker 并行
cf run -n 3
```

每个 Worker 的执行流程：

1. 通过文件锁从任务队列领取一个 approved 任务
2. 创建独立的 git worktree：`.claude-flow/worktrees/task-xxx/`
3. 在 worktree 中运行 Claude Code
4. 成功后合并到主分支，清理 worktree，标记 done
5. 失败则标记 failed，保留日志
6. 循环领取下一个任务

### 查看执行状态

```bash
cf status
# Total tasks: 5
#   approved: 1
#   done: 3
#   failed: 1
```

### 查看日志

```bash
cf log task-a1b2c3
```

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
  "max_workers": 2,                // 最大并行 Worker 数
  "main_branch": "main",           // 主分支名称
  "claude_args": [],                // 传递给 Claude Code 的额外参数
  "auto_merge": true,              // 任务完成后是否自动合并
  "merge_strategy": "--no-ff",     // 合并策略
  "skip_permissions": true,        // 使用 --dangerously-skip-permissions
  "task_timeout": 600,             // 任务超时时间（秒）
  "plan_prompt_prefix": "请分析以下任务并输出实施计划，不要执行代码:",
  "task_prompt_prefix": "你的任务是:"
}
```

### 常用配置场景

**保守模式**（禁用自动合并，手动检查每个任务的产出）：

```json
{
  "auto_merge": false,
  "max_workers": 1
}
```

**高并发模式**（适合大量独立任务）：

```json
{
  "max_workers": 4,
  "auto_merge": true,
  "task_timeout": 1200
}
```

---

## 完整使用示例

```bash
# 初始化
cd my-web-app
cf init

# 添加任务
cf task add -p "实现 GET /api/users 接口，返回分页用户列表" "用户列表 API"
cf task add -p "实现 POST /api/users 接口，包含输入校验" "创建用户 API"
cf task add -p "为用户 API 编写 pytest 测试，覆盖正常和异常路径" "用户 API 测试"

# 生成并审批计划
cf plan
cf plan review

# 并行执行
cf run -n 2

# 检查结果
cf status
cf log task-xxx

# 处理失败任务
cf retry
cf run
```

---

## 注意事项

1. **Git 仓库要求**：项目必须是 Git 仓库，且至少有一次 commit
2. **分支冲突**：多 Worker 并行时可能产生合并冲突，冲突任务会标记为 failed (CONFLICT)
3. **Claude Code**：需要确保 `claude` 命令可用且已配置 API key
4. **文件锁**：使用 `fcntl.flock` 实现，仅支持 Linux/macOS
