# Web 端打通整个流程 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Claude Flow 的 Web 看板覆盖完整的任务生命周期（pending → planning → planned → approved → running → done/failed），所有 CLI 核心操作均可在 Web 上完成。

**Architecture:** 在现有 Flask API 蓝图 (`web/api.py`) 上新增 Plan 生成/查看/反馈、Worker 管理、日志查看等 REST 端点，并引入 SSE（Server-Sent Events）实现实时状态推送。前端 (`index.html`) 对应扩展 UI 面板。后端长任务（plan 生成、worker 执行）通过后台线程运行，避免阻塞 Flask 请求。

**Tech Stack:** Python 3.10+, Flask, SSE (无额外依赖), threading, 现有 TaskManager/Planner/Worker/Monitor 模块

---

## 阶段一：基础设施准备 (Task 1-3)

### Task 1: Flask 依赖声明 + Web 测试骨架

**Files:**
- Modify: `pyproject.toml:10` (dependencies 行)
- Create: `tests/test_web_api.py`
- Create: `tests/conftest_web.py`

**Step 1: 修改 pyproject.toml 添加 web optional dependency**

```toml
[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov"]
web = ["flask>=3.0"]
```

**Step 2: 创建 Web 测试 fixture 文件**

```python
# tests/conftest_web.py
"""Web API 测试共享 fixture。"""
from __future__ import annotations

import pytest
from pathlib import Path
from claude_flow.config import Config


@pytest.fixture
def web_client(git_repo):
    """创建 Flask 测试客户端，使用 git_repo 作为项目根目录。"""
    # 初始化 .claude-flow 目录结构
    cf_dir = git_repo / ".claude-flow"
    for sub in ["logs", "plans", "worktrees"]:
        (cf_dir / sub).mkdir(parents=True, exist_ok=True)

    cfg = Config()
    cfg.save(git_repo)

    from claude_flow.web import create_app
    app = create_app(git_repo, cfg)
    app.config["TESTING"] = True

    with app.test_client() as client:
        yield client
```

**Step 3: 创建 test_web_api.py 基础测试**

```python
# tests/test_web_api.py
"""Web API 端点集成测试。"""
from __future__ import annotations

import json
import pytest

# 使用 conftest_web 中的 web_client fixture
from tests.conftest_web import web_client  # noqa: F401


class TestTaskAPI:
    """任务 CRUD API 测试。"""

    def test_list_tasks_empty(self, web_client):
        resp = web_client.get("/api/tasks")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"] == []

    def test_create_task(self, web_client):
        resp = web_client.post("/api/tasks", json={
            "title": "测试任务",
            "prompt": "测试 prompt",
            "priority": 1,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["title"] == "测试任务"
        assert data["data"]["status"] == "pending"

    def test_create_task_missing_fields(self, web_client):
        resp = web_client.post("/api/tasks", json={"title": "no prompt"})
        assert resp.status_code == 400

    def test_get_task(self, web_client):
        # 先创建
        create_resp = web_client.post("/api/tasks", json={
            "title": "t1", "prompt": "p1"
        })
        task_id = create_resp.get_json()["data"]["id"]
        # 再获取
        resp = web_client.get(f"/api/tasks/{task_id}")
        assert resp.get_json()["data"]["id"] == task_id

    def test_delete_task(self, web_client):
        create_resp = web_client.post("/api/tasks", json={
            "title": "t1", "prompt": "p1"
        })
        task_id = create_resp.get_json()["data"]["id"]
        resp = web_client.delete(f"/api/tasks/{task_id}")
        assert resp.get_json()["ok"] is True

    def test_global_status(self, web_client):
        resp = web_client.get("/api/status")
        data = resp.get_json()
        assert data["ok"] is True
        assert "total" in data["data"]
        assert "counts" in data["data"]
```

**Step 4: 运行测试确认基础绿色**

Run: `pytest tests/test_web_api.py -v`
Expected: 所有测试 PASS（验证现有 API 的正确性）

**Step 5: Commit**

```
feat(web): add Flask optional dependency and web API test skeleton
```

---

### Task 2: 后台任务执行器 (BackgroundRunner)

**Files:**
- Create: `claude_flow/web/runner.py`
- Create: `tests/test_web_runner.py`

**Step 1: 编写 BackgroundRunner 的 failing test**

```python
# tests/test_web_runner.py
"""BackgroundRunner 单元测试。"""
from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock


def test_runner_submit_and_status():
    """测试提交任务并查询状态。"""
    from claude_flow.web.runner import BackgroundRunner

    runner = BackgroundRunner()
    job_id = runner.submit("test-job", lambda: "result-ok")

    assert job_id == "test-job"
    # 等待完成
    for _ in range(50):
        status = runner.get_status(job_id)
        if status["state"] == "done":
            break
        time.sleep(0.05)

    assert status["state"] == "done"
    assert status["result"] == "result-ok"


def test_runner_submit_failure():
    """测试任务失败的状态报告。"""
    from claude_flow.web.runner import BackgroundRunner

    runner = BackgroundRunner()

    def failing_fn():
        raise RuntimeError("boom")

    job_id = runner.submit("fail-job", failing_fn)
    for _ in range(50):
        status = runner.get_status(job_id)
        if status["state"] == "failed":
            break
        time.sleep(0.05)

    assert status["state"] == "failed"
    assert "boom" in status["error"]


def test_runner_get_unknown_job():
    """测试查询不存在的任务。"""
    from claude_flow.web.runner import BackgroundRunner

    runner = BackgroundRunner()
    status = runner.get_status("nonexistent")
    assert status is None
```

**Step 2: 运行测试验证失败**

Run: `pytest tests/test_web_runner.py -v`
Expected: FAIL — ImportError (runner 模块不存在)

**Step 3: 实现 BackgroundRunner**

```python
# claude_flow/web/runner.py
"""后台任务执行器，用于在线程中运行长耗时操作（plan 生成、worker 启动等）。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional


@dataclass
class JobStatus:
    """后台任务状态。"""
    job_id: str
    state: str = "running"  # running | done | failed
    result: Any = None
    error: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None


class BackgroundRunner:
    """线程池式后台任务执行器。

    使用 daemon 线程执行耗时操作，通过 job_id 查询状态。
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, JobStatus] = {}
        self._lock = threading.Lock()

    def submit(self, job_id: str, fn: Callable[[], Any]) -> str:
        """提交后台任务。

        Args:
            job_id: 任务标识（通常用 task_id 或自定义标识）。
            fn: 要在后台执行的可调用对象。

        Returns:
            job_id
        """
        status = JobStatus(job_id=job_id)
        with self._lock:
            self._jobs[job_id] = status

        thread = threading.Thread(
            target=self._run, args=(job_id, fn), daemon=True
        )
        thread.start()
        return job_id

    def _run(self, job_id: str, fn: Callable[[], Any]) -> None:
        try:
            result = fn()
            with self._lock:
                self._jobs[job_id].state = "done"
                self._jobs[job_id].result = result
                self._jobs[job_id].completed_at = datetime.now().isoformat()
        except Exception as e:
            with self._lock:
                self._jobs[job_id].state = "failed"
                self._jobs[job_id].error = str(e)
                self._jobs[job_id].completed_at = datetime.now().isoformat()

    def get_status(self, job_id: str) -> Optional[dict]:
        """查询任务状态。返回 dict 或 None（不存在时）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {
                "job_id": job.job_id,
                "state": job.state,
                "result": job.result,
                "error": job.error,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
            }

    def is_running(self, job_id: str) -> bool:
        """检查指定任务是否正在运行。"""
        with self._lock:
            job = self._jobs.get(job_id)
            return job is not None and job.state == "running"
```

**Step 4: 运行测试验证通过**

Run: `pytest tests/test_web_runner.py -v`
Expected: PASS

**Step 5: Commit**

```
feat(web): add BackgroundRunner for async task execution
```

---

### Task 3: SSE 实时事件推送端点

**Files:**
- Create: `claude_flow/web/sse.py`
- Modify: `claude_flow/web/app.py:47-48` (注册 SSE 蓝图)

**Step 1: 编写 SSE EventBus 和 Blueprint**

```python
# claude_flow/web/sse.py
"""Server-Sent Events (SSE) 实现，用于实时推送任务状态变更和 Worker 活动。"""
from __future__ import annotations

import json
import queue
import time
import threading
from typing import Generator

try:
    from flask import Blueprint, Response
except ImportError:
    raise ImportError("Flask 未安装。")


class EventBus:
    """简单的发布-订阅事件总线，支持多个 SSE 客户端同时监听。"""

    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        """创建新的订阅者队列。"""
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """移除订阅者。"""
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not q]

    def publish(self, event_type: str, data: dict) -> None:
        """向所有订阅者发布事件。"""
        message = {"type": event_type, **data}
        with self._lock:
            dead: list[queue.Queue] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(message)
                except queue.Full:
                    dead.append(q)
            # 清理满队列的订阅者
            for q in dead:
                self._subscribers = [s for s in self._subscribers if s is not q]


# 全局事件总线实例
event_bus = EventBus()

sse_bp = Blueprint("sse", __name__, url_prefix="/api")


@sse_bp.route("/events")
def sse_stream():
    """SSE 端点，客户端通过 EventSource 连接后持续接收事件。"""
    def generate() -> Generator[str, None, None]:
        q = event_bus.subscribe()
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    # 心跳保持连接
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            event_bus.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

**Step 2: 修改 app.py 注册 SSE 蓝图并将 EventBus + BackgroundRunner 注入 app.config**

在 `claude_flow/web/app.py` 中 `app.register_blueprint(api_bp)` 之后添加：

```python
    # 注册 SSE 蓝图
    from .sse import sse_bp, event_bus
    app.register_blueprint(sse_bp)

    # 注册后台任务执行器
    from .runner import BackgroundRunner
    runner = BackgroundRunner()

    app.config["EVENT_BUS"] = event_bus
    app.config["RUNNER"] = runner
```

**Step 3: 运行现有测试确保无回归**

Run: `pytest tests/ -v`
Expected: 所有 PASS

**Step 4: Commit**

```
feat(web): add SSE EventBus for real-time event streaming
```

---

## 阶段二：打通 Plan 生成流程 (Task 4-5)

### Task 4: Plan 生成/查看/反馈 API

**Files:**
- Modify: `claude_flow/web/api.py` (新增 5 个端点)
- Create: `tests/test_web_plan_api.py`

**新增端点：**

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/tasks/<id>/plan` | 触发 plan 生成（后台线程） |
| GET | `/api/tasks/<id>/plan` | 查看最新 plan 内容 |
| POST | `/api/tasks/<id>/plan/feedback` | 提交反馈重新生成 plan |
| GET | `/api/tasks/<id>/plan/versions` | 查看 plan 版本列表 |
| GET | `/api/jobs/<job_id>` | 查询后台任务状态 |

**Step 1: 编写 plan API 的 failing tests**

```python
# tests/test_web_plan_api.py
"""Plan 相关 API 端点测试。"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from tests.conftest_web import web_client  # noqa: F401


class TestPlanAPI:

    def _create_task(self, client):
        """辅助方法：创建一个测试任务。"""
        resp = client.post("/api/tasks", json={
            "title": "plan-test", "prompt": "test prompt"
        })
        return resp.get_json()["data"]["id"]

    @patch("claude_flow.planner.subprocess.run")
    def test_generate_plan(self, mock_run, web_client):
        """测试触发 plan 生成。"""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="# Plan\nStep 1\nStep 2", stderr=""
        )
        task_id = self._create_task(web_client)
        resp = web_client.post(f"/api/tasks/{task_id}/plan")
        data = resp.get_json()
        assert data["ok"] is True
        assert "job_id" in data["data"]

    def test_generate_plan_not_found(self, web_client):
        resp = web_client.post("/api/tasks/nonexistent/plan")
        assert resp.status_code == 404

    def test_get_plan_no_plan(self, web_client):
        task_id = self._create_task(web_client)
        resp = web_client.get(f"/api/tasks/{task_id}/plan")
        assert resp.status_code == 404

    def test_get_job_status(self, web_client):
        resp = web_client.get("/api/jobs/nonexistent")
        assert resp.status_code == 404
```

**Step 2: 运行测试验证失败**

Run: `pytest tests/test_web_plan_api.py -v`
Expected: FAIL

**Step 3: 在 api.py 中实现 plan 相关端点**

在 `claude_flow/web/api.py` 末尾追加：

```python
# -- Plan 生成 / 查看 / 反馈 -------------------------------------------------

@api_bp.route("/tasks/<task_id>/plan", methods=["POST"])
def generate_plan(task_id: str):
    """触发 plan 生成（后台线程执行）。"""
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    runner = current_app.config["RUNNER"]
    event_bus = current_app.config["EVENT_BUS"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    if task.status not in (TaskStatus.PENDING, TaskStatus.PLANNED):
        return _err(f"任务 {task_id} 当前状态为 {task.status.value}，无法生成计划")

    # 防止重复提交
    if runner.is_running(f"plan-{task_id}"):
        return _err(f"任务 {task_id} 的计划正在生成中")

    def _do_plan():
        plan_file = planner.generate(task)
        if plan_file:
            tm.update_status(task_id, TaskStatus.PLANNED)
            event_bus.publish("task_updated", {"task_id": task_id, "status": "planned"})
            return str(plan_file)
        else:
            tm.update_status(task_id, TaskStatus.FAILED, task.error)
            event_bus.publish("task_updated", {"task_id": task_id, "status": "failed"})
            raise RuntimeError(task.error or "Plan generation failed")

    job_id = runner.submit(f"plan-{task_id}", _do_plan)
    tm.update_status(task_id, TaskStatus.PLANNING)
    event_bus.publish("task_updated", {"task_id": task_id, "status": "planning"})

    return _ok({"job_id": job_id, "message": "计划生成已启动"})


@api_bp.route("/tasks/<task_id>/plan", methods=["GET"])
def get_plan(task_id: str):
    """获取任务的最新计划内容。"""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    if not task.plan_file:
        return _err(f"任务 {task_id} 尚无计划文件", 404)

    from pathlib import Path
    plan_path = Path(task.plan_file)
    if not plan_path.exists():
        return _err(f"计划文件不存在: {task.plan_file}", 404)

    content = plan_path.read_text()
    return _ok({"task_id": task_id, "content": content, "plan_file": task.plan_file})


@api_bp.route("/tasks/<task_id>/plan/feedback", methods=["POST"])
def plan_feedback(task_id: str):
    """提交反馈并重新生成 plan（后台线程执行）。"""
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    runner = current_app.config["RUNNER"]
    event_bus = current_app.config["EVENT_BUS"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    data = request.get_json(silent=True) or {}
    feedback = data.get("feedback", "")
    if not feedback:
        return _err("feedback 不能为空")

    if runner.is_running(f"plan-{task_id}"):
        return _err(f"任务 {task_id} 的计划正在生成中")

    def _do_feedback():
        plan_file = planner.generate_interactive(task, feedback=feedback)
        if plan_file:
            tm.update_status(task_id, TaskStatus.PLANNED)
            event_bus.publish("task_updated", {"task_id": task_id, "status": "planned"})
            return str(plan_file)
        else:
            raise RuntimeError(task.error or "Plan regeneration failed")

    job_id = runner.submit(f"plan-{task_id}", _do_feedback)
    tm.update_status(task_id, TaskStatus.PLANNING)

    return _ok({"job_id": job_id, "message": "计划重新生成已启动"})


@api_bp.route("/tasks/<task_id>/plan/versions", methods=["GET"])
def plan_versions(task_id: str):
    """获取任务的所有计划版本列表。"""
    planner = current_app.config["PLANNER"]
    versions = planner.list_versions(task_id)
    result = []
    for v in versions:
        result.append({
            "filename": v.name,
            "path": str(v),
        })
    return _ok(result)


# -- 后台任务状态 -------------------------------------------------------------

@api_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job_status(job_id: str):
    """查询后台任务执行状态。"""
    runner = current_app.config["RUNNER"]
    status = runner.get_status(job_id)
    if status is None:
        return _err(f"任务 {job_id} 不存在", 404)
    return _ok(status)
```

**Step 4: 同时需要将 Planner 构造时传入 task_manager**

修改 `claude_flow/web/app.py:39`，将 Planner 构造改为：

```python
    planner = Planner(project_root, plans_dir, config, task_manager=task_manager)
```

**Step 5: 运行测试验证通过**

Run: `pytest tests/test_web_plan_api.py tests/test_web_api.py -v`
Expected: PASS

**Step 6: Commit**

```
feat(web): add plan generation, viewing, feedback and version APIs
```

---

### Task 5: 前端 Plan 生成/查看 UI

**Files:**
- Modify: `claude_flow/web/templates/index.html`

**Step 1: 在卡片操作区增加 Plan 相关按钮**

修改 `renderCard()` 函数中的 `actionsHtml` 逻辑，为 `pending` 状态增加"生成计划"按钮，为 `planned` 状态增加"查看计划"按钮：

```javascript
// 在 status === 'planned' 的判断之前，增加 pending 状态的按钮
if (status === 'pending') {
    actionsHtml = '<div class="card-actions">'
        + '<button class="btn btn-primary btn-small" onclick="generatePlan(event, \'' + task.id + '\')">Generate Plan</button>'
        + '<button class="btn btn-danger btn-small" onclick="deleteTask(event, \'' + task.id + '\')">Delete</button>'
        + '</div>';
} else if (status === 'planning') {
    actionsHtml = '<div class="card-actions">'
        + '<span style="color:#a29bfe;font-size:12px;">Plan 生成中...</span>'
        + '</div>';
} else if (status === 'planned') {
    actionsHtml = '<div class="card-actions">'
        + '<button class="btn btn-primary btn-small" onclick="viewPlan(event, \'' + task.id + '\')">View Plan</button>'
        + '<button class="btn btn-success btn-small" onclick="approveTask(event, \'' + task.id + '\')">Approve</button>'
        + '<button class="btn btn-warning btn-small" onclick="feedbackPlan(event, \'' + task.id + '\')">Feedback</button>'
        + '<button class="btn btn-danger btn-small" onclick="openRejectModal(event, \'' + task.id + '\')">Reject</button>'
        + '</div>';
}
```

**Step 2: 增加 Plan 查看模态框**

在 HTML `<div class="toast">` 之前添加 plan 查看模态框：

```html
<!-- Plan 查看模态框 -->
<div class="modal-overlay" id="planModal">
    <div class="modal" style="max-width:700px;max-height:80vh;overflow:auto;">
        <h2>Plan 详情</h2>
        <pre id="planContent" style="white-space:pre-wrap;word-break:break-word;font-size:13px;color:#c0c0d0;line-height:1.6;max-height:60vh;overflow-y:auto;background:#0f0f23;padding:16px;border-radius:8px;"></pre>
        <div class="modal-actions">
            <button class="btn btn-cancel" onclick="closePlanModal()">关闭</button>
        </div>
    </div>
</div>

<!-- Plan 反馈模态框 -->
<div class="modal-overlay" id="feedbackModal">
    <div class="modal">
        <h2>Plan 反馈</h2>
        <div class="form-group">
            <label for="feedbackText">你的反馈（将基于此重新生成 Plan）</label>
            <textarea id="feedbackText" placeholder="请输入对 Plan 的改进建议..."></textarea>
        </div>
        <div class="modal-actions">
            <button class="btn btn-cancel" onclick="closeFeedbackModal()">取消</button>
            <button class="btn btn-primary" onclick="submitFeedback()">提交反馈</button>
        </div>
    </div>
</div>
```

**Step 3: 增加对应的 JS 函数**

```javascript
/* -- Plan 操作 --------------------------------------------------------- */
async function generatePlan(event, taskId) {
    event.stopPropagation();
    const data = await api('/tasks/' + taskId + '/plan', { method: 'POST' });
    if (data !== null) {
        showToast('Plan 生成已启动: ' + data.job_id);
        fetchTasks();
    }
}

async function viewPlan(event, taskId) {
    event.stopPropagation();
    const data = await api('/tasks/' + taskId + '/plan');
    if (data !== null) {
        document.getElementById('planContent').textContent = data.content;
        document.getElementById('planModal').classList.add('active');
    }
}

function closePlanModal() {
    document.getElementById('planModal').classList.remove('active');
}

let feedbackTaskId = null;

function feedbackPlan(event, taskId) {
    event.stopPropagation();
    feedbackTaskId = taskId;
    document.getElementById('feedbackText').value = '';
    document.getElementById('feedbackModal').classList.add('active');
}

function closeFeedbackModal() {
    feedbackTaskId = null;
    document.getElementById('feedbackModal').classList.remove('active');
}

async function submitFeedback() {
    if (!feedbackTaskId) return;
    const feedback = document.getElementById('feedbackText').value.trim();
    if (!feedback) {
        showToast('请输入反馈内容', true);
        return;
    }
    const data = await api('/tasks/' + feedbackTaskId + '/plan/feedback', {
        method: 'POST',
        body: JSON.stringify({ feedback: feedback }),
    });
    if (data !== null) {
        showToast('Plan 重新生成已启动');
        closeFeedbackModal();
        fetchTasks();
    }
}

// 模态框外部点击关闭
document.getElementById('planModal').addEventListener('click', function(e) {
    if (e.target === this) closePlanModal();
});
document.getElementById('feedbackModal').addEventListener('click', function(e) {
    if (e.target === this) closeFeedbackModal();
});
```

**Step 4: 手动验证 — 启动 Web 界面确认 UI 正确渲染**

Run: `cd /tmp && mkdir test-project && cd test-project && git init && cf init && cf web`
Expected: 浏览器打开 http://localhost:8080，看到看板界面。创建任务后可看到"Generate Plan"按钮。

**Step 5: Commit**

```
feat(web): add plan generation, viewing and feedback UI
```

---

## 阶段三：打通 Worker 执行流程 (Task 6-7)

### Task 6: Worker 管理 API（启动/停止/状态）

**Files:**
- Modify: `claude_flow/web/api.py` (新增 3 个端点)
- Modify: `claude_flow/web/app.py` (注入 WorktreeManager)
- Create: `tests/test_web_worker_api.py`

**新增端点：**

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/workers/start` | 启动 N 个 Worker（后台线程） |
| POST | `/api/workers/stop` | 停止所有 Worker |
| GET | `/api/tasks/<id>/log` | 获取任务执行日志 |

**Step 1: 编写 Worker 管理 API 的 failing tests**

```python
# tests/test_web_worker_api.py
"""Worker 管理 API 测试。"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from tests.conftest_web import web_client  # noqa: F401


class TestWorkerAPI:

    @patch("claude_flow.worker.subprocess.run")
    def test_start_workers(self, mock_run, web_client):
        resp = web_client.post("/api/workers/start", json={"num_workers": 1})
        data = resp.get_json()
        assert data["ok"] is True

    def test_start_workers_invalid(self, web_client):
        resp = web_client.post("/api/workers/start", json={"num_workers": 0})
        assert resp.status_code == 400

    def test_worker_status(self, web_client):
        resp = web_client.get("/api/workers")
        data = resp.get_json()
        assert data["ok"] is True

    def test_get_log_not_found(self, web_client):
        resp = web_client.get("/api/tasks/nonexistent/log")
        data = resp.get_json()
        assert data["ok"] is False
```

**Step 2: 运行测试验证失败**

Run: `pytest tests/test_web_worker_api.py -v`
Expected: FAIL

**Step 3: 修改 app.py 注入 WorktreeManager**

在 `create_app` 函数中，`planner = ...` 之后添加：

```python
    from ..worktree import WorktreeManager
    worktree_manager = WorktreeManager(
        project_root, project_root / config.worktree_dir
    )
    app.config["WORKTREE_MANAGER"] = worktree_manager
```

**Step 4: 在 api.py 中实现 Worker 管理端点**

```python
# -- Worker 管理 --------------------------------------------------------------

@api_bp.route("/workers/start", methods=["POST"])
def start_workers():
    """启动 Worker（后台线程执行）。body: {num_workers, daemon}"""
    from ..worker import Worker

    tm = current_app.config["TASK_MANAGER"]
    wt = current_app.config["WORKTREE_MANAGER"]
    cfg = current_app.config["CF_CONFIG"]
    runner = current_app.config["RUNNER"]
    event_bus = current_app.config["EVENT_BUS"]
    project_root = current_app.config["PROJECT_ROOT"]

    data = request.get_json(silent=True) or {}
    num_workers = data.get("num_workers", 1)
    daemon_mode = data.get("daemon", False)

    if not isinstance(num_workers, int) or num_workers < 1:
        return _err("num_workers 必须是正整数")

    if num_workers > cfg.max_workers:
        return _err(f"num_workers 不能超过 max_workers ({cfg.max_workers})")

    job_ids = []
    for wid in range(num_workers):
        job_id = f"worker-{wid}"
        if runner.is_running(job_id):
            continue  # 已在运行，跳过

        def _make_run(worker_id, is_daemon):
            def _run():
                w = Worker(worker_id, project_root, tm, wt, cfg)
                if is_daemon:
                    return w.run_daemon()
                return w.run_loop()
            return _run

        runner.submit(job_id, _make_run(wid, daemon_mode))
        job_ids.append(job_id)

    event_bus.publish("workers_started", {"count": len(job_ids)})
    return _ok({"started": job_ids, "daemon": daemon_mode})


@api_bp.route("/workers/stop", methods=["POST"])
def stop_workers():
    """通知 Worker 停止（仅 daemon 模式有效，设置停止标志）。"""
    # 注意：当前 Worker 的 stop 机制依赖 signal，在线程中需要不同实现。
    # 此处通过删除状态文件 + 设置标志来通知停止。
    event_bus = current_app.config["EVENT_BUS"]
    event_bus.publish("workers_stop_requested", {})
    return _ok({"message": "已发送停止请求"})


# -- 任务日志 ----------------------------------------------------------------

@api_bp.route("/tasks/<task_id>/log", methods=["GET"])
def get_task_log(task_id: str):
    """获取任务的执行日志。"""
    project_root = current_app.config["PROJECT_ROOT"]
    log_file = project_root / ".claude-flow" / "logs" / f"{task_id}.log"

    if not log_file.exists():
        return _err(f"任务 {task_id} 的日志不存在", 404)

    content = log_file.read_text()
    # 限制返回大小（最后 50KB）
    max_size = 50 * 1024
    if len(content) > max_size:
        content = "...(truncated)...\n" + content[-max_size:]

    return _ok({"task_id": task_id, "log": content})
```

**Step 5: 运行测试验证通过**

Run: `pytest tests/test_web_worker_api.py -v`
Expected: PASS

**Step 6: Commit**

```
feat(web): add worker start/stop and task log APIs
```

---

### Task 7: 前端 Worker 控制 + 日志查看 + SSE 实时更新

**Files:**
- Modify: `claude_flow/web/templates/index.html`

**Step 1: 在 header 区域增加 Worker 控制按钮**

修改 `<div class="header-actions">` 区域：

```html
<div class="header-actions">
    <div class="status-bar" id="statusBar">加载中...</div>
    <button class="btn btn-success" onclick="startWorkers()">Start Workers</button>
    <button class="btn btn-danger" onclick="stopWorkers()">Stop Workers</button>
    <button class="btn btn-primary" onclick="openNewTaskModal()">+ 新建任务</button>
</div>
```

**Step 2: 增加日志查看模态框**

```html
<!-- 日志查看模态框 -->
<div class="modal-overlay" id="logModal">
    <div class="modal" style="max-width:800px;max-height:80vh;">
        <h2>执行日志</h2>
        <pre id="logContent" style="white-space:pre-wrap;word-break:break-word;font-size:12px;color:#a0a0b0;line-height:1.5;max-height:60vh;overflow-y:auto;background:#0f0f23;padding:16px;border-radius:8px;font-family:'SF Mono','Fira Code',monospace;"></pre>
        <div class="modal-actions">
            <button class="btn btn-cancel" onclick="closeLogModal()">关闭</button>
        </div>
    </div>
</div>
```

**Step 3: 在卡片操作中为 running/done/failed 状态增加"查看日志"按钮**

在 `renderCard()` 中修改对应的 actionsHtml：

```javascript
} else if (status === 'running') {
    actionsHtml = '<div class="card-actions">'
        + '<button class="btn btn-primary btn-small" onclick="viewLog(event, \'' + task.id + '\')">View Log</button>'
        + '</div>';
} else if (status === 'done') {
    actionsHtml = '<div class="card-actions">'
        + '<button class="btn btn-primary btn-small" onclick="viewLog(event, \'' + task.id + '\')">View Log</button>'
        + '</div>';
} else if (status === 'failed') {
    actionsHtml = '<div class="card-actions">'
        + '<button class="btn btn-primary btn-small" onclick="viewLog(event, \'' + task.id + '\')">View Log</button>'
        + '<button class="btn btn-warning btn-small" onclick="retryTask(event, \'' + task.id + '\')">Retry</button>'
        + '<button class="btn btn-danger btn-small" onclick="deleteTask(event, \'' + task.id + '\')">Delete</button>'
        + '</div>';
}
```

**Step 4: 增加 JS Worker 控制和日志查看函数**

```javascript
/* -- Worker 控制 ------------------------------------------------------- */
async function startWorkers() {
    const num = prompt('启动 Worker 数量 (1-4):', '1');
    if (!num) return;
    const n = parseInt(num, 10);
    if (isNaN(n) || n < 1) {
        showToast('请输入有效的数字', true);
        return;
    }
    const data = await api('/workers/start', {
        method: 'POST',
        body: JSON.stringify({ num_workers: n, daemon: false }),
    });
    if (data !== null) {
        showToast('已启动 ' + data.started.length + ' 个 Worker');
    }
}

async function stopWorkers() {
    const data = await api('/workers/stop', { method: 'POST' });
    if (data !== null) {
        showToast('已发送停止请求');
    }
}

/* -- 日志查看 ---------------------------------------------------------- */
async function viewLog(event, taskId) {
    event.stopPropagation();
    const data = await api('/tasks/' + taskId + '/log');
    if (data !== null) {
        document.getElementById('logContent').textContent = data.log;
        document.getElementById('logModal').classList.add('active');
    }
}

function closeLogModal() {
    document.getElementById('logModal').classList.remove('active');
}

document.getElementById('logModal').addEventListener('click', function(e) {
    if (e.target === this) closeLogModal();
});
```

**Step 5: 替换轮询为 SSE 实时更新**

将原来的 `startAutoRefresh()` 改为 SSE 模式（同时保留轮询作为降级方案）：

```javascript
/* -- SSE 实时更新 ------------------------------------------------------ */
function startSSE() {
    if (typeof EventSource === 'undefined') {
        // 降级为轮询
        startAutoRefresh();
        return;
    }
    const evtSource = new EventSource('/api/events');
    evtSource.onmessage = function(event) {
        try {
            const data = JSON.parse(event.data);
            // 收到任何事件都刷新任务列表
            fetchTasks();
        } catch(e) {
            // ignore parse errors
        }
    };
    evtSource.onerror = function() {
        // SSE 断开，降级为轮询
        evtSource.close();
        startAutoRefresh();
    };
}
```

然后将初始化部分的 `startAutoRefresh()` 改为 `startSSE()`。

**Step 6: 手动验证完整流程**

Run: 启动 `cf web`，在浏览器中：
1. 创建任务 -> 看到 Pending 列出现
2. 点击 "Generate Plan" -> 卡片移到 Planning -> 完成后移到 Planned
3. 点击 "View Plan" -> 弹窗显示 Plan 内容
4. 点击 "Approve" -> 卡片移到 Approved
5. 点击 "Start Workers" -> 卡片移到 Running -> 最终到 Done
6. 点击 "View Log" -> 弹窗显示日志

Expected: 全流程可在 Web 上完成

**Step 7: Commit**

```
feat(web): add worker controls, log viewer and SSE real-time updates
```

---

## 阶段四：增强功能 (Task 8-9)

### Task 8: 批量操作 + 初始化 API

**Files:**
- Modify: `claude_flow/web/api.py`

**新增端点：**

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/init` | 初始化 `.claude-flow/` 目录 |
| POST | `/api/tasks/plan-all` | 批量为所有 pending 任务生成 plan |
| POST | `/api/tasks/approve-all` | 批量审批所有 planned 任务 |

**Step 1: 实现批量操作端点**

```python
@api_bp.route("/init", methods=["POST"])
def init_project():
    """初始化 .claude-flow/ 目录结构。"""
    project_root = current_app.config["PROJECT_ROOT"]
    cf_dir = project_root / ".claude-flow"
    for sub in ["logs", "plans", "worktrees"]:
        (cf_dir / sub).mkdir(parents=True, exist_ok=True)

    cfg = current_app.config["CF_CONFIG"]
    cfg.save(project_root)
    return _ok({"message": f"已初始化 {cf_dir}"})


@api_bp.route("/tasks/plan-all", methods=["POST"])
def plan_all_tasks():
    """批量为所有 pending 任务生成 plan。"""
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    runner = current_app.config["RUNNER"]
    event_bus = current_app.config["EVENT_BUS"]

    pending = tm.list_tasks(status=TaskStatus.PENDING)
    if not pending:
        return _ok({"message": "没有待规划的任务", "count": 0})

    job_ids = []
    for task in pending:
        job_id = f"plan-{task.id}"
        if runner.is_running(job_id):
            continue

        task_id = task.id
        def _make_plan(t_id, t_obj):
            def _do():
                plan_file = planner.generate(t_obj)
                if plan_file:
                    tm.update_status(t_id, TaskStatus.PLANNED)
                    event_bus.publish("task_updated", {"task_id": t_id, "status": "planned"})
                    return str(plan_file)
                else:
                    tm.update_status(t_id, TaskStatus.FAILED, t_obj.error)
                    raise RuntimeError(t_obj.error or "Plan failed")
            return _do

        runner.submit(job_id, _make_plan(task_id, task))
        tm.update_status(task_id, TaskStatus.PLANNING)
        job_ids.append(job_id)

    return _ok({"message": f"已启动 {len(job_ids)} 个计划生成任务", "job_ids": job_ids})


@api_bp.route("/tasks/approve-all", methods=["POST"])
def approve_all_tasks():
    """批量审批所有 planned 任务。"""
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    event_bus = current_app.config["EVENT_BUS"]

    planned = tm.list_tasks(status=TaskStatus.PLANNED)
    approved = []
    for task in planned:
        planner.approve(task)
        tm.update_status(task.id, TaskStatus.APPROVED)
        approved.append(task.id)

    event_bus.publish("tasks_batch_approved", {"count": len(approved)})
    return _ok({"approved": approved, "count": len(approved)})
```

**Step 2: 前端增加批量操作按钮**

在 header 区域添加下拉菜单或按钮组。

**Step 3: Commit**

```
feat(web): add batch plan-all, approve-all and project init APIs
```

---

### Task 9: 完善 Web 测试覆盖

**Files:**
- Modify: `tests/test_web_api.py`
- Modify: `tests/test_web_plan_api.py`
- Modify: `tests/test_web_worker_api.py`

**目标测试覆盖：**

- 所有新增 API 端点的 happy path 和 error path
- SSE EventBus 的 publish/subscribe 测试
- BackgroundRunner 的边界情况（重复提交、并发查询）
- Plan 生成/反馈的 mock 测试

**Step 1: 补充缺失测试用例**

针对每个新端点编写至少 2 个测试（正常 + 异常）。

**Step 2: 运行全量测试**

Run: `pytest tests/ -v --tb=short`
Expected: 所有 PASS

**Step 3: Commit**

```
test(web): comprehensive test coverage for web API endpoints
```

---

## 任务依赖关系

```
Task 1 (测试骨架)
  └─> Task 2 (BackgroundRunner)
       └─> Task 3 (SSE)
            └─> Task 4 (Plan API) ──> Task 5 (Plan UI)
            └─> Task 6 (Worker API) ──> Task 7 (Worker UI + SSE)
                                           └─> Task 8 (批量操作)
                                                └─> Task 9 (测试完善)
```

## 文件变更总结

| 操作 | 文件路径 |
|------|----------|
| Create | `claude_flow/web/runner.py` |
| Create | `claude_flow/web/sse.py` |
| Create | `tests/conftest_web.py` |
| Create | `tests/test_web_api.py` |
| Create | `tests/test_web_runner.py` |
| Create | `tests/test_web_plan_api.py` |
| Create | `tests/test_web_worker_api.py` |
| Modify | `claude_flow/web/app.py` (注入 SSE, Runner, WorktreeManager) |
| Modify | `claude_flow/web/api.py` (新增 ~10 个端点) |
| Modify | `claude_flow/web/templates/index.html` (Plan/Worker/Log UI + SSE) |
| Modify | `pyproject.toml` (添加 web optional dependency) |
