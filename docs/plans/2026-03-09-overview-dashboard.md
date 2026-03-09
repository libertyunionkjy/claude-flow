# Overview Dashboard 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在前端侧边栏 MANAGEMENT 区域新增 Overview 栏，展示 Claude Flow 的总体状态，包含任务统计仪表盘、活跃 Worker 状态、最近活动流和系统健康状态。

**Architecture:** 后端新增 `GET /api/overview` 聚合端点，一次性返回所有 Overview 数据（任务分布、Worker 状态、最近活动、系统配置）。前端新增 Overview 页面容器和 `toggleOverviewPage()` 切换逻辑，复用已有的 `usage-card` 等 CSS 模式。通过 SRP 原则，聚合逻辑全部在后端完成，前端仅负责渲染。

**Tech Stack:** Flask (后端), 原生 JavaScript + CSS (前端), pytest (测试)

---

### Task 1: 后端 — 新增 `/api/overview` 聚合端点

**Files:**
- Modify: `claude_flow/web/api.py:433-476` (在 status/workers 区域之后添加)
- Test: `tests/test_web_api.py`

**Step 1: 在 `tests/test_web_api.py` 末尾添加 Overview 测试类**

在文件末尾（`TestRunAPI` 类之后）追加：

```python
# -- Overview ----------------------------------------------------------------


class TestOverview:
    def test_overview_empty(self, client):
        """Overview returns valid structure with no tasks."""
        resp = client.get("/api/overview")
        data = resp.get_json()
        assert data["ok"] is True
        d = data["data"]
        assert d["tasks"]["total"] == 0
        assert d["tasks"]["completion_rate"] == 0
        assert d["workers"]["active"] == 0
        assert isinstance(d["recent_activity"], list)
        assert isinstance(d["system"], dict)
        assert "max_workers" in d["system"]

    def test_overview_with_tasks(self, client, tm):
        """Overview correctly aggregates task stats."""
        t1 = tm.add("T1", "P1")
        t2 = tm.add("T2", "P2")
        t3 = tm.add("T3", "P3")
        tm.update_status(t2.id, TaskStatus.DONE)
        tm.update_status(t3.id, TaskStatus.RUNNING)
        resp = client.get("/api/overview")
        data = resp.get_json()
        d = data["data"]
        assert d["tasks"]["total"] == 3
        assert d["tasks"]["by_status"]["pending"] == 1
        assert d["tasks"]["by_status"]["done"] == 1
        assert d["tasks"]["by_status"]["running"] == 1
        assert d["tasks"]["completion_rate"] == pytest.approx(1 / 3, abs=0.01)

    def test_overview_recent_activity(self, client, tm):
        """Overview includes recent activity entries for completed/failed tasks."""
        t1 = tm.add("Done Task", "P1")
        tm.update_status(t1.id, TaskStatus.DONE)
        t2 = tm.add("Failed Task", "P2")
        tm.update_status(t2.id, TaskStatus.FAILED, error="some error")
        resp = client.get("/api/overview")
        data = resp.get_json()
        activity = data["data"]["recent_activity"]
        assert len(activity) >= 2
        types = [a["type"] for a in activity]
        assert "completed" in types
        assert "failed" in types

    def test_overview_workers_from_tasks(self, client, tm):
        """Overview infers active workers from running tasks."""
        t1 = tm.add("Running", "P1")
        tm.update_status(t1.id, TaskStatus.APPROVED)
        # Simulate a running task with worker_id
        tasks = tm._load()
        for t in tasks:
            if t.id == t1.id:
                t.status = TaskStatus.RUNNING
                t.worker_id = 1
                from datetime import datetime
                t.started_at = datetime.now()
        tm._with_lock(lambda: tm._save(tasks))
        resp = client.get("/api/overview")
        data = resp.get_json()
        assert data["data"]["workers"]["active"] == 1
        assert len(data["data"]["workers"]["details"]) == 1

    def test_overview_system_info(self, client, web_app):
        """Overview returns system configuration info."""
        resp = client.get("/api/overview")
        data = resp.get_json()
        system = data["data"]["system"]
        assert "project_root" in system
        assert "auto_merge" in system
        assert "max_workers" in system
        assert "worktree_count" in system
```

**Step 2: 运行测试确认全部失败**

Run: `cd /opt/shared/claude-flow && python -m pytest tests/test_web_api.py::TestOverview -v`
Expected: FAIL — 404 Not Found (路由不存在)

**Step 3: 在 `api.py` 中实现 `/api/overview` 端点**

在 `api.py` 的 `worker_status()` 函数之后（约第 477 行）插入：

```python
@api_bp.route("/overview", methods=["GET"])
def overview():
    """获取 Overview 聚合数据：任务统计、Worker 状态、最近活动、系统信息。"""
    tm = current_app.config["TASK_MANAGER"]
    config = current_app.config["CF_CONFIG"]
    project_root = current_app.config["PROJECT_ROOT"]
    tasks = tm.list_tasks()

    # -- 任务统计 --
    by_status = {}
    for status in TaskStatus:
        by_status[status.value] = 0
    for t in tasks:
        by_status[t.status.value] += 1

    total = len(tasks)
    done_count = by_status.get("done", 0)
    completion_rate = done_count / total if total > 0 else 0

    # -- Worker 状态 --
    running_tasks = [t for t in tasks if t.status == TaskStatus.RUNNING]
    worker_details = []
    for t in running_tasks:
        elapsed = None
        if t.started_at:
            from datetime import datetime
            delta = datetime.now() - t.started_at
            minutes, seconds = divmod(int(delta.total_seconds()), 60)
            hours, minutes = divmod(minutes, 60)
            if hours > 0:
                elapsed = f"{hours}h{minutes:02d}m{seconds:02d}s"
            else:
                elapsed = f"{minutes}m{seconds:02d}s"
        worker_details.append({
            "worker_id": t.worker_id,
            "task_id": t.id,
            "task_title": t.title,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "elapsed": elapsed,
        })

    # -- 最近活动 --
    activity = []
    for t in tasks:
        if t.status == TaskStatus.DONE and t.completed_at:
            activity.append({
                "type": "completed",
                "task_id": t.id,
                "title": t.title,
                "timestamp": t.completed_at.isoformat(),
            })
        elif t.status == TaskStatus.FAILED:
            ts = t.completed_at or t.started_at or t.created_at
            activity.append({
                "type": "failed",
                "task_id": t.id,
                "title": t.title,
                "error": t.error,
                "timestamp": ts.isoformat() if ts else None,
            })
        elif t.status == TaskStatus.RUNNING and t.started_at:
            activity.append({
                "type": "started",
                "task_id": t.id,
                "title": t.title,
                "timestamp": t.started_at.isoformat(),
            })
    # Sort by timestamp descending, limit to 20
    activity.sort(key=lambda a: a.get("timestamp") or "", reverse=True)
    activity = activity[:20]

    # -- 系统信息 --
    worktree_dir = project_root / config.worktree_dir
    worktree_count = 0
    if worktree_dir.exists():
        worktree_count = len([d for d in worktree_dir.iterdir() if d.is_dir()])

    system = {
        "project_root": str(project_root),
        "auto_merge": config.auto_merge,
        "merge_mode": config.merge_mode,
        "max_workers": config.max_workers,
        "skip_permissions": config.skip_permissions,
        "task_timeout": config.task_timeout,
        "worktree_count": worktree_count,
        "web_port": config.web_port,
    }

    return _ok({
        "tasks": {
            "total": total,
            "by_status": by_status,
            "completion_rate": round(completion_rate, 4),
        },
        "workers": {
            "active": len(running_tasks),
            "max": config.max_workers,
            "details": worker_details,
        },
        "recent_activity": activity,
        "system": system,
    })
```

**Step 4: 运行测试确认全部通过**

Run: `cd /opt/shared/claude-flow && python -m pytest tests/test_web_api.py::TestOverview -v`
Expected: ALL PASS

**Step 5: 运行完整测试套件确认无回归**

Run: `cd /opt/shared/claude-flow && python -m pytest tests/ -v`
Expected: ALL PASS

---

### Task 2: 前端 — Overview 页面 CSS 样式

**Files:**
- Modify: `claude_flow/web/templates/index.html` (CSS 区域，约第 576 行之前插入)

**Step 1: 在 Usage Dashboard CSS 之前插入 Overview 样式**

在 `index.html` 第 575 行（`/* -- Usage Dashboard */` 注释之前）插入以下 CSS：

```css
/* -- Overview Dashboard -------------------------------------------------- */
.overview-page {
    display: none;
    padding: 24px;
    max-width: 1100px;
    margin: 0 auto;
    overflow-y: auto;
}
.overview-page.active { display: block; }

.overview-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
}
.overview-header h2 { font-size: 22px; color: #00d4ff; margin: 0; }
.overview-header .overview-refresh-btn {
    background: none;
    border: 1px solid #2a2a4a;
    color: #8888aa;
    padding: 6px 14px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
    font-family: inherit;
    transition: all 0.15s ease;
}
.overview-header .overview-refresh-btn:hover {
    border-color: #00d4ff;
    color: #00d4ff;
}

.overview-section {
    margin-bottom: 28px;
}
.overview-section-title {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: #6a6a8a;
    margin-bottom: 14px;
    font-weight: 600;
}

/* Task Stats Cards - reuse usage-card pattern */
.overview-stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-bottom: 8px;
}
.overview-stat-card {
    background: #16213e;
    border: 1px solid #2a2a4a;
    border-radius: 10px;
    padding: 16px;
    text-align: center;
    transition: border-color 0.2s ease;
}
.overview-stat-card:hover { border-color: #3a3a6a; }
.overview-stat-card .stat-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #6a6a8a;
    margin-bottom: 6px;
}
.overview-stat-card .stat-value {
    font-size: 26px;
    font-weight: 700;
    color: #00d4ff;
    line-height: 1.2;
}
.overview-stat-card .stat-value.success { color: #00b894; }
.overview-stat-card .stat-value.warning { color: #fdcb6e; }
.overview-stat-card .stat-value.danger { color: #ff6b6b; }

/* Status Distribution Bar */
.overview-distribution {
    display: flex;
    height: 8px;
    border-radius: 4px;
    overflow: hidden;
    background: #1a1a3a;
    margin-top: 12px;
}
.overview-distribution .dist-seg {
    height: 100%;
    transition: width 0.3s ease;
}
.overview-distribution-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-top: 10px;
    font-size: 12px;
    color: #8888aa;
}
.overview-distribution-legend .legend-item {
    display: flex;
    align-items: center;
    gap: 5px;
}
.overview-distribution-legend .legend-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}

/* Worker Cards */
.overview-workers {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
}
.overview-worker-card {
    background: #16213e;
    border: 1px solid #2a2a4a;
    border-radius: 10px;
    padding: 16px;
    transition: border-color 0.2s ease;
}
.overview-worker-card:hover { border-color: #3a3a6a; }
.overview-worker-card .worker-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
}
.overview-worker-card .worker-id {
    font-size: 13px;
    font-weight: 600;
    color: #00d4ff;
}
.overview-worker-card .worker-elapsed {
    font-size: 12px;
    color: #fdcb6e;
    font-variant-numeric: tabular-nums;
}
.overview-worker-card .worker-task {
    font-size: 12px;
    color: #b0b0cc;
}
.overview-worker-card .worker-task-id {
    color: #6a6a8a;
    font-size: 11px;
    margin-top: 2px;
}

.overview-workers-empty {
    color: #4a4a6a;
    font-size: 13px;
    padding: 16px;
    text-align: center;
    background: #16213e;
    border: 1px dashed #2a2a4a;
    border-radius: 10px;
}

/* Activity Feed */
.overview-activity {
    display: flex;
    flex-direction: column;
    gap: 0;
}
.overview-activity-item {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 10px 12px;
    border-bottom: 1px solid rgba(42, 42, 74, 0.4);
    transition: background 0.15s ease;
}
.overview-activity-item:hover { background: rgba(255, 255, 255, 0.02); }
.overview-activity-item:last-child { border-bottom: none; }
.overview-activity-item .activity-icon {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    flex-shrink: 0;
    margin-top: 2px;
}
.overview-activity-item .activity-icon.completed { background: rgba(0, 184, 148, 0.15); color: #00b894; }
.overview-activity-item .activity-icon.failed { background: rgba(255, 107, 107, 0.15); color: #ff6b6b; }
.overview-activity-item .activity-icon.started { background: rgba(253, 203, 110, 0.15); color: #fdcb6e; }
.overview-activity-item .activity-body { flex: 1; min-width: 0; }
.overview-activity-item .activity-title {
    font-size: 13px;
    color: #c0c0d0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.overview-activity-item .activity-meta {
    font-size: 11px;
    color: #6a6a8a;
    margin-top: 2px;
}
.overview-activity-empty {
    color: #4a4a6a;
    font-size: 13px;
    padding: 24px;
    text-align: center;
}

/* System Info Grid */
.overview-system {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 10px;
}
.overview-sys-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 14px;
    background: #16213e;
    border: 1px solid #2a2a4a;
    border-radius: 8px;
    font-size: 13px;
}
.overview-sys-item .sys-label { color: #6a6a8a; }
.overview-sys-item .sys-value { color: #c0c0d0; font-weight: 500; }
.overview-sys-item .sys-value.on { color: #00b894; }
.overview-sys-item .sys-value.off { color: #ff6b6b; }
```

**Step 2: 验证 CSS 语法无误**

手动检查无语法错误即可。此步在后续集成测试中验证。

---

### Task 3: 前端 — Overview 页面 HTML 容器

**Files:**
- Modify: `claude_flow/web/templates/index.html` (侧边栏 + 主内容区)

**Step 1: 在侧边栏 Management 区添加 Overview 导航按钮**

在 `index.html` 第 1600 行（`<button class="nav-item" data-tab="all_tasks"` 之前）插入：

```html
<button class="nav-item" id="overviewToggleBtn" onclick="toggleOverviewPage()">
    <span class="nav-dot" style="background: #a29bfe;"></span>
    <span class="nav-label">Overview</span>
</button>
```

**Step 2: 在主内容区添加 Overview 页面容器**

在 `index.html` 的 `usagePage` div 之后（约第 1878 行后）插入：

```html
<!-- Overview Dashboard -->
<div class="overview-page" id="overviewPage">
    <div class="overview-header">
        <h2>Overview</h2>
        <button class="overview-refresh-btn" onclick="fetchOverviewData()">Refresh</button>
    </div>

    <!-- Task Statistics -->
    <div class="overview-section">
        <div class="overview-section-title">Task Statistics</div>
        <div class="overview-stats" id="overviewStats">
            <div class="overview-stat-card">
                <div class="stat-label">Total</div>
                <div class="stat-value" id="ovTotal">-</div>
            </div>
            <div class="overview-stat-card">
                <div class="stat-label">Completion Rate</div>
                <div class="stat-value success" id="ovRate">-</div>
            </div>
            <div class="overview-stat-card">
                <div class="stat-label">Running</div>
                <div class="stat-value warning" id="ovRunning">-</div>
            </div>
            <div class="overview-stat-card">
                <div class="stat-label">Failed</div>
                <div class="stat-value danger" id="ovFailed">-</div>
            </div>
        </div>
        <div class="overview-distribution" id="ovDistribution"></div>
        <div class="overview-distribution-legend" id="ovLegend"></div>
    </div>

    <!-- Active Workers -->
    <div class="overview-section">
        <div class="overview-section-title">Active Workers (<span id="ovWorkerCount">0</span> / <span id="ovWorkerMax">0</span>)</div>
        <div class="overview-workers" id="ovWorkers">
            <div class="overview-workers-empty">No active workers</div>
        </div>
    </div>

    <!-- Recent Activity -->
    <div class="overview-section">
        <div class="overview-section-title">Recent Activity</div>
        <div class="overview-activity" id="ovActivity">
            <div class="overview-activity-empty">No recent activity</div>
        </div>
    </div>

    <!-- System Info -->
    <div class="overview-section">
        <div class="overview-section-title">System Configuration</div>
        <div class="overview-system" id="ovSystem"></div>
    </div>
</div>
```

---

### Task 4: 前端 — Overview JavaScript 逻辑

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JavaScript 区域)

**Step 1: 添加 Overview 全局状态变量**

在全局变量区域（约第 2165 行附近 `let allTasks = [];` 之后）添加：

```javascript
let overviewVisible = false;
```

**Step 2: 在 `switchTab()` 函数中添加 Overview 页面隐藏逻辑**

在 `switchTab()` 函数中（约第 2280-2286 行的 "Hide all views" 区域），追加隐藏 overviewPage 的逻辑：

```javascript
document.getElementById('overviewPage').classList.remove('active');
overviewVisible = false;
var overviewBtn = document.getElementById('overviewToggleBtn');
overviewBtn.querySelector('.nav-label').textContent = 'Overview';
```

**Step 3: 在 Usage/Guide 的 toggle 函数中互斥隐藏 Overview**

在 `toggleUsagePage()` 函数开头（约第 3771 行），在 `if (guideVisible)` 块之后添加：

```javascript
if (overviewVisible) {
    document.getElementById('overviewPage').classList.remove('active');
    document.getElementById('overviewToggleBtn').querySelector('.nav-label').textContent = 'Overview';
    overviewVisible = false;
}
```

在 `toggleGuidePage()` 函数开头（约第 3722 行），在 `if (usageVisible)` 块之后添加同样的代码：

```javascript
if (overviewVisible) {
    document.getElementById('overviewPage').classList.remove('active');
    document.getElementById('overviewToggleBtn').querySelector('.nav-label').textContent = 'Overview';
    overviewVisible = false;
}
```

**Step 4: 添加 `toggleOverviewPage()` 函数**

在 Usage Dashboard 函数区域之前（`toggleGuidePage` 之后）添加：

```javascript
/* -- Overview Dashboard -------------------------------------------------- */

function toggleOverviewPage() {
    if (currentView === 'detail') { closeTaskDetail(); }
    if (guideVisible) {
        document.getElementById('guidePage').classList.remove('active');
        document.getElementById('guideToggleBtn').querySelector('.nav-label').textContent = 'Workflow Guide';
        guideVisible = false;
    }
    if (usageVisible) {
        document.getElementById('usagePage').classList.remove('active');
        document.getElementById('usageToggleBtn').querySelector('.nav-label').textContent = 'Token Usage';
        usageVisible = false;
    }

    overviewVisible = !overviewVisible;
    var page = document.getElementById('overviewPage');
    var contentPanel = document.getElementById('contentPanel');
    var topBar = document.getElementById('topBar');
    var btn = document.getElementById('overviewToggleBtn');

    if (overviewVisible) {
        page.classList.add('active');
        contentPanel.style.display = 'none';
        topBar.style.display = 'none';
        document.getElementById('detailView').classList.remove('active');
        document.getElementById('allTasksPanel').classList.remove('active');
        btn.querySelector('.nav-label').textContent = 'Back to Board';
        document.querySelectorAll('.nav-item[data-tab]').forEach(function(item) {
            item.classList.remove('active');
        });
        fetchOverviewData();
    } else {
        page.classList.remove('active');
        contentPanel.style.display = '';
        topBar.style.display = '';
        btn.querySelector('.nav-label').textContent = 'Overview';
        switchTab(activeTab);
    }
}

async function fetchOverviewData() {
    var data = await api('/overview');
    if (!data || !data.ok) return;
    var d = data.data;
    renderOverviewStats(d.tasks);
    renderOverviewWorkers(d.workers);
    renderOverviewActivity(d.recent_activity);
    renderOverviewSystem(d.system);
}

function renderOverviewStats(tasks) {
    document.getElementById('ovTotal').textContent = tasks.total;
    document.getElementById('ovRate').textContent = Math.round(tasks.completion_rate * 100) + '%';
    document.getElementById('ovRunning').textContent = tasks.by_status.running || 0;
    document.getElementById('ovFailed').textContent = tasks.by_status.failed || 0;

    // Distribution bar
    var bar = document.getElementById('ovDistribution');
    var legend = document.getElementById('ovLegend');
    if (tasks.total === 0) {
        bar.innerHTML = '';
        legend.innerHTML = '';
        return;
    }
    var segs = '';
    var legendHtml = '';
    var statuses = ['pending', 'planning', 'planned', 'approved', 'running', 'needs_input', 'done', 'failed'];
    statuses.forEach(function(s) {
        var count = tasks.by_status[s] || 0;
        if (count === 0) return;
        var pct = (count / tasks.total * 100).toFixed(1);
        var color = STATUS_COLORS[s] || '#888';
        segs += '<div class="dist-seg" style="width:' + pct + '%;background:' + color + ';" title="' + (STATUS_LABELS[s] || s) + ': ' + count + '"></div>';
        legendHtml += '<span class="legend-item"><span class="legend-dot" style="background:' + color + ';"></span>' + (STATUS_LABELS[s] || s) + ' ' + count + '</span>';
    });
    bar.innerHTML = segs;
    legend.innerHTML = legendHtml;
}

function renderOverviewWorkers(workers) {
    document.getElementById('ovWorkerCount').textContent = workers.active;
    document.getElementById('ovWorkerMax').textContent = workers.max;
    var container = document.getElementById('ovWorkers');
    if (workers.details.length === 0) {
        container.innerHTML = '<div class="overview-workers-empty">No active workers</div>';
        return;
    }
    container.innerHTML = workers.details.map(function(w) {
        return '<div class="overview-worker-card">'
            + '<div class="worker-header">'
            + '<span class="worker-id">Worker #' + (w.worker_id != null ? w.worker_id : '?') + '</span>'
            + '<span class="worker-elapsed">' + (w.elapsed || '-') + '</span>'
            + '</div>'
            + '<div class="worker-task">' + escapeHtml(w.task_title || 'Unknown') + '</div>'
            + '<div class="worker-task-id">' + (w.task_id || '') + '</div>'
            + '</div>';
    }).join('');
}

function renderOverviewActivity(activity) {
    var container = document.getElementById('ovActivity');
    if (!activity || activity.length === 0) {
        container.innerHTML = '<div class="overview-activity-empty">No recent activity</div>';
        return;
    }
    container.innerHTML = activity.map(function(a) {
        var iconMap = { completed: '&#10003;', failed: '&#10007;', started: '&#9654;' };
        var icon = iconMap[a.type] || '&#8226;';
        var ts = a.timestamp ? new Date(a.timestamp).toLocaleString() : '';
        var meta = a.type.charAt(0).toUpperCase() + a.type.slice(1);
        if (ts) meta += ' &middot; ' + ts;
        if (a.error) meta += ' &middot; ' + escapeHtml(a.error);
        return '<div class="overview-activity-item">'
            + '<div class="activity-icon ' + a.type + '">' + icon + '</div>'
            + '<div class="activity-body">'
            + '<div class="activity-title">' + escapeHtml(a.title || a.task_id) + '</div>'
            + '<div class="activity-meta">' + meta + '</div>'
            + '</div>'
            + '</div>';
    }).join('');
}

function renderOverviewSystem(system) {
    var container = document.getElementById('ovSystem');
    var items = [
        { label: 'Project Root', value: system.project_root, raw: true },
        { label: 'Max Workers', value: system.max_workers },
        { label: 'Auto Merge', value: system.auto_merge, bool: true },
        { label: 'Merge Mode', value: system.merge_mode },
        { label: 'Skip Permissions', value: system.skip_permissions, bool: true },
        { label: 'Task Timeout', value: system.task_timeout + 's' },
        { label: 'Active Worktrees', value: system.worktree_count },
        { label: 'Web Port', value: system.web_port },
    ];
    container.innerHTML = items.map(function(item) {
        var valClass = 'sys-value';
        var display = String(item.value);
        if (item.bool) {
            valClass += item.value ? ' on' : ' off';
            display = item.value ? 'ON' : 'OFF';
        }
        if (item.raw) {
            display = '<span style="font-size:11px;word-break:break-all;">' + escapeHtml(String(item.value)) + '</span>';
        }
        return '<div class="overview-sys-item">'
            + '<span class="sys-label">' + item.label + '</span>'
            + '<span class="' + valClass + '">' + display + '</span>'
            + '</div>';
    }).join('');
}
```

**Step 5: 确认存在 `escapeHtml` 辅助函数**

搜索 `index.html` 中是否已有 `escapeHtml` 函数。如果没有，在 Overview 函数之前添加：

```javascript
function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
```

---

### Task 5: 前端 — 修复视图互斥与 Detail View 集成

**Files:**
- Modify: `claude_flow/web/templates/index.html`

**Step 1: 在 `openTaskDetail()` 函数中隐藏 Overview**

在 `openTaskDetail()` 函数内（约第 3029 行），找到隐藏其他页面的逻辑，追加：

```javascript
document.getElementById('overviewPage').classList.remove('active');
overviewVisible = false;
document.getElementById('overviewToggleBtn').querySelector('.nav-label').textContent = 'Overview';
```

**Step 2: 验证互斥完整性**

确保以下所有视图切换函数都会隐藏 overviewPage：
- `switchTab()`
- `toggleUsagePage()`
- `toggleGuidePage()`
- `openTaskDetail()`

---

### Task 6: 完整集成验证

**Files:**
- All modified files

**Step 1: 运行 Overview API 测试**

Run: `cd /opt/shared/claude-flow && python -m pytest tests/test_web_api.py::TestOverview -v`
Expected: ALL PASS

**Step 2: 运行完整测试套件**

Run: `cd /opt/shared/claude-flow && python -m pytest tests/ -v`
Expected: ALL PASS

**Step 3: 启动开发服务器手动验证**

Run: `cd /opt/shared/claude-flow && cf web` (或直接 `python -m flask run`)
手动验证：
1. 侧边栏 MANAGEMENT 区显示 "Overview" 按钮（在 All Tasks 上方）
2. 点击 Overview 进入仪表盘页面
3. 任务统计卡片显示正确数据
4. 分布条形图按状态着色
5. Worker 区域正确显示（有/无运行中 Worker）
6. 最近活动列表按时间倒序显示
7. 系统配置信息正确展示
8. 点击 "Back to Board" 正确返回
9. 切换到 Token Usage / Workflow Guide 时 Overview 被正确隐藏
10. 从其他页面切换到 Overview 时互斥正确

---

## 变更文件汇总

| 文件 | 操作 | 变更说明 |
|------|------|---------|
| `claude_flow/web/api.py` | Modify | 新增 `GET /api/overview` 聚合端点 (~70 行) |
| `claude_flow/web/templates/index.html` | Modify | CSS (~190 行) + HTML (~50 行) + JS (~180 行) |
| `tests/test_web_api.py` | Modify | 新增 `TestOverview` 测试类 (5 个测试用例) |

## 预估工作量

| Task | 预估时间 |
|------|---------|
| Task 1: 后端 API | 15 min |
| Task 2: CSS 样式 | 10 min |
| Task 3: HTML 容器 | 5 min |
| Task 4: JS 逻辑 | 20 min |
| Task 5: 视图互斥 | 5 min |
| Task 6: 集成验证 | 10 min |
| **总计** | **~65 min** |
