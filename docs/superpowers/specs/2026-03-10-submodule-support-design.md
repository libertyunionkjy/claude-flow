# Git Submodule 支持设计方案

## 背景

Claude Flow 使用 Git Worktree 实现任务的版本隔离，但 `git worktree add` 不会自动初始化 submodule。当目标项目包含 submodule 且任务需要修改 submodule 内部代码时，Claude Code 在 worktree 中无法访问 submodule 内容。

## 使用场景

- Claude Code **主要修改 submodule 内部代码**，主项目只需更新 submodule 引用指针
- 典型项目：3 个 submodule，其中 2 个较大
- 每个任务通常只涉及 1-2 个 submodule

## 设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工作隔离方式 | 主项目 worktree + 选择性 submodule init | worktree 与主仓库共享 `.git/modules/`，init 本质是 checkout，速度快；Claude Code 能看到主项目完整上下文 |
| 指定 submodule | CLI 显式 `-s` / `--submodule` 参数 | 简单直接，不影响无 submodule 项目 |
| 提交流程 | 自动两步提交 | submodule 先提交 → 主项目更新指针并提交 → merge 回 main |
| 远端推送 | 不推送 submodule | submodule 远端推送由用户/CI 另行处理 |

## 数据模型变更

### Task dataclass

```python
@dataclass
class Task:
    # ... 现有字段 ...
    submodules: List[str] = field(default_factory=list)  # submodule 相对路径列表
```

- 存储 submodule 在项目中的相对路径（如 `libs/core`, `packages/ui`）
- 默认空列表，完全向后兼容
- 序列化/反序列化同步更新

## WorktreeManager 变更

### `create()` 扩展

```python
def create(self, task_id: str, branch: str, config: Config = None,
           submodules: List[str] = None) -> Path:
    # ... 现有 worktree 创建逻辑 ...

    if submodules:
        self._init_submodules(wt_path, submodules)

    return wt_path
```

### 新增 `_init_submodules()`

```python
def _init_submodules(self, wt_path: Path, submodules: List[str]) -> None:
    """在 worktree 中选择性初始化指定的 submodule。

    只初始化任务指定的 submodule，不触碰其他 submodule。
    利用主仓库 .git/modules/ 共享对象存储，update 本质是 checkout。
    """
    for sub_path in submodules:
        self._run(["git", "submodule", "init", sub_path], cwd=wt_path)
        self._run(["git", "submodule", "update", sub_path], cwd=wt_path)
```

- 初始化失败时抛出 `CalledProcessError`，由调用方处理（任务标记 FAILED）

## Worker 提交流程变更

### `_auto_commit()` 两步提交

```python
def _auto_commit(self, task: Task, wt_path: Path) -> bool:
    # 步骤 1: 对每个 submodule 单独提交
    for sub_path in task.submodules:
        sub_dir = wt_path / sub_path
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(sub_dir), capture_output=True, text=True,
        )
        if status.stdout.strip():
            subprocess.run(["git", "add", "-A"], cwd=str(sub_dir), ...)
            subprocess.run(
                ["git", "commit", "-m", f"feat({task.id}): {task.title}",
                 "--no-verify"],
                cwd=str(sub_dir), ...
            )

    # 步骤 2: 主项目提交（包含 submodule 指针更新 + 其他改动）
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(wt_path), capture_output=True, text=True,
    )
    if not status.stdout.strip():
        return False

    subprocess.run(["git", "add", "-A"], cwd=str(wt_path), ...)
    subprocess.run(
        ["git", "commit", "-m", f"feat({task.id}): {task.title}",
         "--no-verify"],
        cwd=str(wt_path), ...
    )
    return True
```

- submodule 无变更时跳过步骤 1 中该 submodule 的提交
- 无 submodule 的任务走原有逻辑（循环不执行）
- merge 流程无需改动，合并的是主项目分支

## CLI 变更

### `cf task add`

```bash
cf task add -p "修改认证逻辑" -s libs/auth "任务标题"
cf task add -p "统一日志格式" -s libs/core -s libs/utils "任务标题"
```

`-s` / `--submodule`: Click `multiple=True`，可重复指定。

### `cf task mini`

```bash
cf task mini -p "修复登录Bug" -s libs/auth "修复标题"
cf task mini -p "修复登录Bug" -s libs/auth "修复标题" --run
```

同样支持 `-s` 参数。

## Web API 变更

### 新增端点

```
GET /api/submodules
```

返回项目中所有 submodule 的路径列表（从 `.gitmodules` 解析）。供前端创建任务时展示选择列表。

### 现有端点扩展

```
POST /api/mini-tasks
{
  "title": "修复标题",
  "prompt": "修复登录Bug",
  "submodules": ["libs/auth"]     // 新增可选字段，默认 []
}

GET /api/mini-tasks
GET /api/tasks
// 响应中每个 task 包含 submodules 字段
```

### Web 前端

- 创建任务表单：新增 submodule 多选控件，可选项从 `GET /api/submodules` 获取
- 任务列表：展示 submodule 标签

## 校验与容错

- **创建任务时校验**：读取 `.gitmodules` 确认指定路径是合法 submodule，无效路径立即报错
- **submodule init 失败**：任务标记 FAILED，错误信息包含具体哪个 submodule 失败
- **submodule 无变更**：跳过该 submodule 的提交，只要主项目有变更仍算成功
- **非 git 仓库**：`-s` 参数被忽略（与现有 non-git mode 行为一致），或给出明确警告

## 测试计划

### 正常流程测试

| 测试 | 说明 |
|------|------|
| 单 submodule 任务创建 | 验证 Task 模型正确存储 submodules |
| worktree + submodule init | 验证 worktree 中目标 submodule 被初始化，其他不受影响 |
| 两步提交 | 验证 submodule 先提交、主项目后提交的顺序 |
| 合并流程 | 验证 merge 后主项目 submodule 指针正确更新 |
| CLI `-s` 参数 | 验证单个和多个 `-s` 参数解析 |
| Web API submodules 字段 | 验证创建/查询接口的 submodules 字段 |
| Mini Task submodule | 验证 Mini Task 的 PTY 启动时 submodule 初始化 |

### 异常场景测试

| 测试 | 说明 |
|------|------|
| 非 git 仓库 | 传入 `-s` 参数时的行为（应忽略或警告） |
| 无效 submodule 路径 | 指定不存在的 submodule 路径，应报错 |
| submodule init 失败 | 模拟初始化失败，任务应标记 FAILED |
| submodule 无变更 | Claude Code 未修改 submodule，只改了主项目代码 |
| 主项目无变更 | Claude Code 只改了 submodule 但主项目无其他改动（指针更新算变更） |
| 向后兼容 | 旧格式 tasks.json 无 submodules 字段时的反序列化 |
| 并发 submodule 任务 | 两个 Worker 同时操作不同 submodule 的隔离性 |

## 影响范围

| 模块 | 改动内容 |
|------|----------|
| `models.py` | Task 新增 `submodules: List[str]` |
| `worktree.py` | `create()` 支持 submodule 初始化，新增 `_init_submodules()` |
| `worker.py` | `_auto_commit()` 两步提交逻辑 |
| `task_manager.py` | 序列化/反序列化兼容新字段 |
| `cli.py` | `task add` / `task mini` 新增 `-s` 参数 |
| `web/api.py` | API 接受/返回 `submodules`，新增 `GET /api/submodules` |
| `web/templates/` | 创建表单增加 submodule 选择 |
| `tests/` | 新增 submodule 相关测试（正常 + 异常） |
