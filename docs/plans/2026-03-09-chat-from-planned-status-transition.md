# Planned 状态下 Chat 应回退到 Planning Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 当 `planned` 状态的任务发起 Chat 交互时，自动将状态回退到 `planning`，保持状态语义一致性。

**Architecture:** 修改 `send_chat` API 的状态转换条件，从仅处理 `PENDING` 扩展到同时处理 `PLANNED`。前端已有逻辑（`planning` + `interactive` 显示 "Open Chat"）完全兼容，无需改动。补充对应的单元测试。

**Tech Stack:** Python (Flask API), pytest

---

### Task 1: 补充测试 — planned 状态下 Chat 应将状态回退到 planning

**Files:**
- Modify: `tests/test_web_api.py` (在 `TestChat` 类中新增测试)

**Step 1: 写失败测试**

在 `tests/test_web_api.py` 的 `TestChat` 类末尾（`test_chat_finalize_no_session` 方法之后），新增测试方法：

```python
def test_chat_send_transitions_planned_to_planning(self, client, tm, web_app):
    """POST /chat on a planned task should transition status to planning."""
    task = tm.add("T1", "P1")
    tm.update_status(task.id, TaskStatus.PLANNING)
    tm.update_status(task.id, TaskStatus.PLANNED)

    with patch("claude_flow.chat.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="AI response", stderr=""
        )
        resp = client.post(
            f"/api/tasks/{task.id}/chat",
            json={"message": "I want to revise the plan"},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["accepted"] is True

    # Verify the task status changed to planning
    updated = tm.get(task.id)
    assert updated.status == TaskStatus.PLANNING
```

注意：需要确保文件顶部已有 `from claude_flow.models import TaskStatus` 的导入。

**Step 2: 运行测试验证失败**

Run: `pytest tests/test_web_api.py::TestChat::test_chat_send_transitions_planned_to_planning -v`
Expected: FAIL — 因为 `send_chat` 只对 `PENDING` 做状态转换，`PLANNED` 任务的状态不会变为 `PLANNING`

---

### Task 2: 实现 — 扩展 send_chat 的状态转换条件

**Files:**
- Modify: `claude_flow/web/api.py:230`

**Step 3: 修改条件判断**

将 `api.py` 第 229-231 行：

```python
# Ensure task is in planning state
if task.status == TaskStatus.PENDING:
    tm.update_status(task_id, TaskStatus.PLANNING)
```

改为：

```python
# Ensure task is in planning state
if task.status in (TaskStatus.PENDING, TaskStatus.PLANNED):
    tm.update_status(task_id, TaskStatus.PLANNING)
```

**Step 4: 运行测试验证通过**

Run: `pytest tests/test_web_api.py::TestChat::test_chat_send_transitions_planned_to_planning -v`
Expected: PASS

**Step 5: 运行全部测试确保无回归**

Run: `pytest tests/ -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add claude_flow/web/api.py tests/test_web_api.py
git commit -m "fix(chat): planned 状态下发起 Chat 应回退到 planning 状态"
```
