# Plan Feedback 工作流重设计

## 问题

当前 plan review 阶段只有 Approve 和 Reject 两个动作。当 Claude 在 plan 中提出澄清问题时，用户只能通过 Reject 来回复，导致：

1. `planner.reject()` 将用户回复包装为 `"注意：上次的方案被拒绝，原因：{用户回复}"`
2. Claude 误解用户意图，认为方案被否定，反复提出新的澄清问题
3. 用户陷入死循环

## 设计方案

### 核心思路

用统一的 **Feedback** 动作替代 Reject。不管是回答问题、要求修改、还是否定方案，都走同一个入口，prompt 使用中性的"用户反馈"措辞。

### 状态流转

```
当前：planned -> [Reject] -> pending -> [Plan] -> planning -> planned
改后：planned -> [Feedback] -> planning -> planned（原地迭代）
```

### 变更清单

#### 1. 后端 API (`claude_flow/web/api.py`)

- 将 `POST /tasks/<task_id>/reject` 改为 `POST /tasks/<task_id>/feedback`
- 请求体：`{ "message": "用户反馈内容" }`
- 允许 `planned` 状态直接进入 `planning`
- 调用 `planner.generate_interactive(task, feedback=message)`
- 完成后回到 `planned`，不经过 `pending`

#### 2. Planner (`claude_flow/planner.py`)

- 删除 `reject()` 方法
- 复用已有的 `generate_interactive()`，其 prompt 构建逻辑：
  - 读取上一版 plan 作为上下文
  - 附加用户反馈（措辞为"用户反馈"而非"被拒绝"）
  - 自动版本管理（`_v{N}.md`）

#### 3. 前端 (`claude_flow/web/templates/index.html`)

- Reject 按钮改为 Feedback 按钮（样式从危险红色改为中性色）
- 弹窗标题：Reject Task -> Feedback
- Textarea placeholder：Enter rejection reason... -> 输入反馈或回复...
- Plan 查看页内的 Reject 按钮同步更改

#### 4. CLI (`claude_flow/cli.py`)

- `cf plan review` 中的 reject 交互同步改为 feedback 语义

### 不变的部分

- Approve 流程不变
- `generate_interactive()` 的版本管理逻辑不变
- 任务的其他状态流转不变
