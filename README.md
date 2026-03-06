# Claude Flow

多实例 Claude Code 工作流管理器。在任意 Git 项目中管理多个 Claude Code 实例并行开发。

灵感来自[胡渊鸣的 Claude Code 工作流](https://mp.weixin.qq.com/s/example)，实现了从任务队列、Git Worktree 隔离到 Web 看板的完整多 Agent 开发流水线。

## 核心能力

- **任务队列** — 维护优先级任务列表，Worker 按优先级自动领取执行
- **Git Worktree 并行化** — 每个 Worker 在独立 worktree 中工作，通过 symlink 共享关键文件
- **Plan Mode 封装** — 批量生成 plan，支持多轮对话反馈，统一 review 后再执行
- **守护进程模式** — Ralph Loop 持续轮询，干完一个活自动接下一个
- **Rebase 合并 + 冲突自动修复** — 使用 rebase 策略合并，冲突时调用 Claude 自动解决
- **合并前测试验证** — 合并前自动运行测试，失败时自动修复重试
- **PROGRESS.md 经验沉淀** — 每次任务完成后记录经验教训和 commit ID
- **Stream JSON 实时监控** — 解析 worker 输出，实时掌握执行进度
- **Web Manager 看板** — 暗色主题看板界面，支持手机端操作

## 安装

```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装
pip install -e .

# 开发模式（含测试依赖）
pip install -e ".[dev]"

# Web 看板（可选）
pip install flask
```

> **注意：** Debian/Ubuntu 系统需先安装 `python3-venv`：`apt install python3-venv`

后续使用前需激活虚拟环境：`source .venv/bin/activate`

要求：Python 3.10+, Git, [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code), Linux/macOS

## 快速开始

```bash
# 1. 在项目中初始化
cd your-project
cf init

# 2. 添加任务（支持优先级）
cf task add -p "实现用户登录 API，包含 JWT 认证" -P 5 "用户登录"
cf task add -p "编写单元测试覆盖所有 API 端点" "API 测试"

# 3. 生成计划并审批
cf plan              # 批量生成 plan
cf plan review       # 交互式审批（支持多轮反馈）

# 4. 执行
cf run               # 单 worker 执行
cf run -n 3          # 3 个并行 worker
cf run --daemon      # 守护进程模式，持续轮询

# 5. 监控
cf watch             # 实时监控 worker 状态
cf web               # 启动 Web 看板界面
cf status            # 查看任务状态总览
cf progress          # 查看经验沉淀日志
```

## 命令总览

| 命令 | 说明 |
|------|------|
| `cf init` | 初始化 `.claude-flow/` 目录 |
| `cf task add "标题"` | 添加任务（`-p` prompt，`-f` 批量导入，`-P` 优先级） |
| `cf task list` | 查看所有任务（按优先级排序） |
| `cf task show <id>` | 查看任务详情 |
| `cf task remove <id>` | 删除任务 |
| `cf plan [-t id]` | 生成计划（可指定单个任务） |
| `cf plan review` | 交互式审批计划（支持 `[f]eedback` 多轮对话） |
| `cf plan approve <id>` | 审批指定计划（`--all` 全部审批） |
| `cf run [-n N] [-d]` | 启动 Worker 执行（`-n` 并行数，`-d` 守护进程模式） |
| `cf watch` | 实时监控 worker 活动 |
| `cf web [--port 8080]` | 启动 Web 看板界面 |
| `cf status` | 查看任务和 worker 状态总览 |
| `cf log <id>` | 查看任务执行日志 |
| `cf progress` | 查看 PROGRESS.md 经验沉淀 |
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
├── config.py         # 配置加载/保存（含全部新增配置项）
├── models.py         # Task / TaskStatus 数据模型
├── task_manager.py   # 任务 CRUD + 优先级队列 + 文件锁
├── worker.py         # Worker 生命周期管理（含守护进程模式）
├── worktree.py       # Git worktree 操作（含 symlink / rebase / push）
├── planner.py        # Plan mode 封装（含多轮对话 / plan 拆分）
├── progress.py       # PROGRESS.md 经验沉淀
├── monitor.py        # Stream JSON 实时解析与监控
└── web/              # Web Manager 看板界面
    ├── __init__.py
    ├── app.py        # Flask 应用工厂
    ├── api.py        # REST API（9 个端点）
    └── templates/
        └── index.html  # 暗色看板 UI
```

## 测试

```bash
pytest -v
```

## 许可证

MIT
