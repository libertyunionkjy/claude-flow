[根目录](../CLAUDE.md) > **tests**

# tests -- 单元测试套件

## 模块职责

`tests/` 目录包含 `claude_flow` 包的完整单元测试套件，使用 pytest 框架，为每个核心模块提供 1:1 的测试覆盖。

## 入口与启动

```bash
# 运行全部测试
pytest -v

# 运行单个测试文件
pytest tests/test_models.py -v

# 运行匹配名称的测试
pytest -k "test_claim" -v
```

## 共享 Fixture

`conftest.py` 定义了共享的 `git_repo` fixture：
- 在 `tmp_path` 下创建临时 git 仓库
- 初始化为 `main` 分支
- 包含初始 commit（一个 README.md）
- 用于 `test_worktree.py`、`test_worker.py`、`test_cli.py` 等需要真实 git 环境的测试

## 测试对应关系

| 测试文件 | 被测模块 | 测试数量 | Mock 策略 |
|----------|----------|----------|-----------|
| `test_models.py` | `models.py` | 6 | 无需 mock |
| `test_config.py` | `config.py` | 5 | 使用 tmp_path |
| `test_task_manager.py` | `task_manager.py` | 10 | 使用 tmp_path |
| `test_planner.py` | `planner.py` | 5 | mock subprocess.run |
| `test_worktree.py` | `worktree.py` | 5 | 真实 git 操作（git_repo） |
| `test_worker.py` | `worker.py` | 3 | mock subprocess.run + git_repo |
| `test_cli.py` | `cli.py` | 4 | CliRunner + git_repo |

## 关键依赖与配置

- `pytest >= 7.0`
- `pytest-cov`（可选，覆盖率统计）
- `click.testing.CliRunner`（CLI 测试）
- `unittest.mock.patch` / `MagicMock`（外部调用隔离）

## 测试模式说明

1. **纯逻辑测试**（`test_models.py`, `test_config.py`）：使用 `tmp_path`，无需 git 环境
2. **文件操作测试**（`test_task_manager.py`）：使用 `tmp_path`，测试 JSON 持久化和文件锁
3. **Git 操作测试**（`test_worktree.py`）：使用 `git_repo` fixture，真实执行 git 命令
4. **集成测试**（`test_worker.py`, `test_cli.py`）：结合 git_repo + mock 外部 CLI 调用

## 相关文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `__init__.py` | 0 | 包标记（空文件） |
| `conftest.py` | 17 | 共享 git_repo fixture |
| `test_models.py` | 73 | Task/TaskStatus 数据模型测试 |
| `test_config.py` | 43 | Config 加载/保存/默认值测试 |
| `test_task_manager.py` | 78 | 任务 CRUD/锁/持久化测试 |
| `test_planner.py` | 58 | Plan 生成/审批/拒绝测试 |
| `test_worktree.py` | 54 | Worktree 创建/删除/合并测试 |
| `test_worker.py` | 49 | Worker 执行/失败/空循环测试 |
| `test_cli.py` | 41 | CLI init/task/status 命令测试 |

## 变更记录 (Changelog)

| 时间 | 操作 |
|------|------|
| 2026-03-05T14:07:01 | 初始化测试文档（init-architect 自适应扫描） |
