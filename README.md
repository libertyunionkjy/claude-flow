# Claude Flow

多实例 Claude Code 工作流管理器。在任意 Git 项目中管理多个 Claude Code 实例并行开发。

## 核心能力

- **任务队列** — 维护任务列表，Worker 自动领取执行
- **Git Worktree 并行化** — 每个 Worker 在独立 worktree 中工作，互不干扰
- **Plan Mode 封装** — 批量生成 plan，统一 review 后再执行

## 安装

```bash
pip install -e .

# 开发模式（含测试依赖）
pip install -e ".[dev]"
```

要求：Python 3.10+, Git, [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)

## 快速开始

```bash
# 1. 在项目中初始化
cd your-project
cf init

# 2. 添加任务
cf task add -p "实现用户登录 API，包含 JWT 认证" "用户登录"
cf task add -p "编写单元测试覆盖所有 API 端点" "API 测试"

# 3. 生成计划并审批
cf plan              # 批量生成 plan
cf plan review       # 交互式审批

# 4. 执行
cf run               # 单 worker 执行
cf run -n 3          # 3 个并行 worker
```

## 命令总览

| 命令 | 说明 |
|------|------|
| `cf init` | 初始化 `.claude-flow/` 目录 |
| `cf task add "标题"` | 添加任务（`-p` 指定 prompt，`-f` 批量导入） |
| `cf task list` | 查看所有任务 |
| `cf task show <id>` | 查看任务详情 |
| `cf task remove <id>` | 删除任务 |
| `cf plan [task_id]` | 生成计划（可指定单个任务） |
| `cf plan review` | 交互式审批计划 |
| `cf plan approve <id>` | 审批指定计划（`--all` 全部审批） |
| `cf run [-n N]` | 启动 Worker 执行任务 |
| `cf status` | 查看任务状态总览 |
| `cf log <id>` | 查看任务执行日志 |
| `cf clean` | 清理 worktree 和已合并分支 |
| `cf reset <id>` | 重置失败任务为 pending |
| `cf retry` | 重试所有失败任务 |

## 任务生命周期

```
pending → planning → planned → (review) → approved → running → merging → done
                                                                    ↘ failed
```

## 项目结构

```
claude_flow/
├── cli.py            # Click CLI 入口
├── config.py         # 配置加载/保存
├── models.py         # Task / TaskStatus 数据模型
├── task_manager.py   # 任务 CRUD + 文件锁
├── worker.py         # Worker 生命周期管理
├── worktree.py       # Git worktree 操作
└── planner.py        # Plan mode 封装
```

## 测试

```bash
pytest -v
```

## 许可证

MIT
