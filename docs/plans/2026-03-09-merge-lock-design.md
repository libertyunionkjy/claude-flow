# 并行 Worker 合并锁设计

## 问题

多个 Worker 并行执行任务后，同时进入合并阶段时会产生竞态条件。Worker A 先合并到 main，导致 Worker B 的 rebase 基底过时，`merge --ff-only` 失败，任务被标记为 `FAILED (CONFLICT)`。

随着 Worker 数量增加（3-5 个），问题频率更高。

## 方案

在合并操作周围加跨进程文件锁（`fcntl.flock`），确保同一时刻只有一个 Worker 操作 main 分支。fetch + rebase + merge 整个流程在锁内执行，保证每个 Worker 合并时 main 是最新的。

## 设计细节

### 1. 合并锁机制

- 锁文件：`.claude-flow/merge.lock`（与 `tasks.lock` 同级）
- 实现位置：`WorktreeManager` 新增 `_with_merge_lock(self, fn)` 方法
- 锁类型：`fcntl.flock(LOCK_EX)`，进程退出自动释放
- 锁粒度：仅覆盖合并操作（秒级），不影响 claude 执行阶段的并行度

### 2. 锁内流程

```
Worker 完成 claude 执行
  -> 获取 merge.lock（阻塞等待）
  -> fetch origin（如有 remote）
  -> rebase（基于最新 main）
  -> checkout main + merge --ff-only
  -> 释放 merge.lock
```

关键点：fetch 和 rebase 必须在锁内执行，不能提前做，否则等拿到锁时 main 可能又变了。

### 3. 异常处理

- **真实代码冲突**：锁内 rebase 仍可能遇到真实冲突（两个任务改同一段代码）。保持现有行为：claude 解决冲突，重试 `max_merge_retries` 次，全部失败标记 `FAILED`。
- **锁等待**：不设超时。claude 解决冲突已有 `task_timeout` 保护，不会无限阻塞。
- **Worker 崩溃**：`fcntl.flock` 绑定 fd，进程退出 OS 自动释放，无死锁风险。

### 4. 改动范围

| 文件 | 改动 |
|------|------|
| `worktree.py` | 新增 `_with_merge_lock`，`rebase_and_merge` 和 `merge` 方法内部加锁 |
| `worker.py` | 无需改动 |
| `test_worktree.py` | 新增 `_with_merge_lock` 互斥性测试 |

### 5. 不做的事情

- 不引入新的任务状态（如 `ready_to_merge`）
- 不引入独立的合并守护进程
- 不对锁等待设置超时
