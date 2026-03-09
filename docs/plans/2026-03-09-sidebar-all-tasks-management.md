# Sidebar "All Tasks" 管理面板 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在左侧 sidebar 新增 "All Tasks" 标签，提供全量任务的搜索、筛选、排序、批量删除等管理功能。

**Architecture:** 后端新增 `POST /api/tasks/batch-delete` 批量删除 API，前端在 `index.html` 中新增 sidebar 入口、工具栏（搜索/筛选/排序控件）和表格视图，通过纯前端内存筛选和排序实现高性能交互，批量操作通过新 API 完成。

**Tech Stack:** Python/Flask (后端)、原生 HTML/CSS/JavaScript (前端，无框架依赖)

---

## 现有代码结构参考

| 文件 | 职责 |
|------|------|
| `claude_flow/web/api.py` | REST API 蓝图，所有 `/api/*` 端点 |
| `claude_flow/web/templates/index.html` | 单文件前端（CSS + HTML + JS 内联） |
| `claude_flow/task_manager.py` | 任务 CRUD，`remove()` 方法用于删除 |
| `claude_flow/models.py` | `Task` dataclass、`TaskStatus` 枚举 |
| `tests/test_web_api.py` | Web API 测试，使用 Flask test client |

---

## Task 1: 后端 - 批量删除 API

**Files:**
- Modify: `claude_flow/web/api.py` (在 `delete_task` 路由后新增)
- Test: `tests/test_web_api.py`

**Step 1: 编写失败测试**

在 `tests/test_web_api.py` 中新增 `TestBatchDelete` 类：

```python
class TestBatchDelete:
    def test_batch_delete_success(self, client, tm):
        t1 = tm.add("T1", "P1")
        t2 = tm.add("T2", "P2")
        t3 = tm.add("T3", "P3")
        resp = client.post(
            "/api/tasks/batch-delete",
            json={"task_ids": [t1.id, t2.id]},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["count"] == 2
        assert t1.id in data["data"]["deleted"]
        assert t2.id in data["data"]["deleted"]
        # t3 should still exist
        assert tm.get(t3.id) is not None

    def test_batch_delete_empty(self, client):
        resp = client.post("/api/tasks/batch-delete", json={"task_ids": []})
        data = resp.get_json()
        assert data["ok"] is False

    def test_batch_delete_missing_field(self, client):
        resp = client.post("/api/tasks/batch-delete", json={})
        data = resp.get_json()
        assert data["ok"] is False

    def test_batch_delete_nonexistent(self, client, tm):
        t1 = tm.add("T1", "P1")
        resp = client.post(
            "/api/tasks/batch-delete",
            json={"task_ids": [t1.id, "nonexistent-id"]},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["count"] == 1
        assert "nonexistent-id" in data["data"]["failed"]
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_web_api.py::TestBatchDelete -v`
Expected: FAIL（路由不存在）

**Step 3: 实现批量删除端点**

在 `claude_flow/web/api.py` 的 `delete_task` 函数后新增：

```python
@api_bp.route("/tasks/batch-delete", methods=["POST"])
def batch_delete_tasks():
    """批量删除任务。body: {task_ids: [str]}"""
    tm = current_app.config["TASK_MANAGER"]
    data = request.get_json(silent=True)

    if not data or not data.get("task_ids"):
        return _err("task_ids 不能为空")

    task_ids = data["task_ids"]
    if not isinstance(task_ids, list):
        return _err("task_ids 必须是数组")

    deleted = []
    failed = []
    for tid in task_ids:
        task = tm.get(tid)
        if task:
            _cleanup_task_resources(tid, task)
        if tm.remove(tid):
            deleted.append(tid)
        else:
            failed.append(tid)

    return _ok({"deleted": deleted, "failed": failed, "count": len(deleted)})
```

关键设计决策：
- 复用 `_cleanup_task_resources()` 确保正在运行的任务被正确停止
- 返回 `deleted` 和 `failed` 列表，前端可据此提示用户
- 部分失败不影响整体响应（非事务性，best-effort）

**Step 4: 运行测试确认通过**

Run: `pytest tests/test_web_api.py::TestBatchDelete -v`
Expected: 4 passed

**Step 5: Commit**

```bash
git add claude_flow/web/api.py tests/test_web_api.py
git commit -m "feat(api): add POST /api/tasks/batch-delete endpoint"
```

---

## Task 2: 前端 - Sidebar "All Tasks" 导航入口

**Files:**
- Modify: `claude_flow/web/templates/index.html`

**Step 1: 在 sidebar-nav 中添加 Management 分组和 All Tasks 按钮**

在 `<nav class="sidebar-nav">` 内部、现有 "Task Status" 分组**之前**新增：

```html
<div class="sidebar-section-label">Management</div>
<button class="nav-item" data-tab="all_tasks" onclick="switchTab('all_tasks')">
    <span class="nav-dot" style="background: #00d4ff;"></span>
    <span class="nav-label">All Tasks</span>
    <span class="nav-count" id="nav-count-all_tasks">0</span>
</button>
<div class="sidebar-section-label">Task Status</div>
```

**Step 2: 添加对应的 CSS 样式**

```css
.nav-item[data-tab="all_tasks"].active { border-left-color: #00d4ff; color: #00d4ff; }
.nav-item[data-tab="all_tasks"] .nav-dot { background: #00d4ff; }
```

**Step 3: 在 `updateNavCounts()` 中更新 All Tasks 计数**

```javascript
// Update All Tasks count
var allCountEl = document.getElementById('nav-count-all_tasks');
if (allCountEl) allCountEl.textContent = allTasks.length;
```

**Step 4: 浏览器中手动验证**

- 打开 Web Manager
- 确认左侧 sidebar 显示 "Management" 分组和 "All Tasks" 按钮
- 确认数字 badge 显示正确的总任务数

**Step 5: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): add All Tasks nav entry in sidebar"
```

---

## Task 3: 前端 - All Tasks 管理面板骨架

**Files:**
- Modify: `claude_flow/web/templates/index.html`

**Step 1: 创建 All Tasks 面板 HTML 结构**

在 `<div class="main-content">` 内部，与 `content-panel`、`detail-view` 平级，新增：

```html
<!-- All Tasks Management Panel -->
<div class="all-tasks-panel" id="allTasksPanel">
    <!-- Toolbar -->
    <div class="all-tasks-toolbar">
        <div class="all-tasks-toolbar-row">
            <input type="text" class="all-tasks-search" id="allTasksSearch"
                   placeholder="Search by ID, title, or prompt..."
                   oninput="filterAllTasks()">
            <div class="filter-group">
                <span class="filter-label">Status</span>
                <select class="filter-select" id="allTasksStatusFilter" onchange="filterAllTasks()">
                    <option value="">All</option>
                    <option value="pending">Pending</option>
                    <option value="planning">Planning</option>
                    <option value="planned">Planned</option>
                    <option value="approved">Approved</option>
                    <option value="running">Running</option>
                    <option value="needs_input">Needs Input</option>
                    <option value="done">Done</option>
                    <option value="failed">Failed</option>
                </select>
            </div>
            <div class="filter-group">
                <span class="filter-label">Priority</span>
                <select class="filter-select" id="allTasksPriorityFilter" onchange="filterAllTasks()">
                    <option value="">All</option>
                    <option value="0">Normal</option>
                    <option value="1">Low (P1)</option>
                    <option value="2">Medium (P2)</option>
                    <option value="3">High (P3)</option>
                </select>
            </div>
            <div class="filter-group">
                <span class="filter-label">Date</span>
                <input type="date" class="filter-date-input" id="allTasksDateFilter"
                       onchange="filterAllTasks()">
            </div>
            <button class="btn btn-outline btn-small" onclick="clearAllFilters()">Clear Filters</button>
        </div>
        <div class="all-tasks-toolbar-row">
            <div class="all-tasks-result-info" id="allTasksResultInfo">0 / 0 tasks</div>
            <div class="batch-actions" id="batchActions" style="display:none;">
                <span class="batch-info" id="batchInfo">0 selected</span>
                <button class="btn btn-danger btn-small" onclick="batchDeleteTasks()">Delete Selected</button>
                <button class="btn btn-outline btn-small" onclick="clearSelection()">Clear Selection</button>
            </div>
        </div>
    </div>
    <!-- Table -->
    <div class="all-tasks-table-wrapper">
        <table class="all-tasks-table">
            <thead>
                <tr>
                    <th class="task-checkbox-cell">
                        <input type="checkbox" id="selectAllCheckbox"
                               onchange="toggleSelectAll(this)">
                    </th>
                    <th data-sort="id" onclick="sortAllTasks('id')">ID <span class="sort-arrow"></span></th>
                    <th data-sort="title" onclick="sortAllTasks('title')">Title <span class="sort-arrow"></span></th>
                    <th data-sort="status" onclick="sortAllTasks('status')">Status <span class="sort-arrow"></span></th>
                    <th data-sort="priority" onclick="sortAllTasks('priority')">Priority <span class="sort-arrow"></span></th>
                    <th data-sort="created_at" onclick="sortAllTasks('created_at')">Created <span class="sort-arrow"></span></th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="allTasksBody"></tbody>
        </table>
        <div class="all-tasks-empty" id="allTasksEmpty" style="display:none;">
            No tasks match the current filters
        </div>
    </div>
</div>
```

**Step 2: 添加 All Tasks 面板 CSS**

关键样式类（约 280 行 CSS）：

- `.all-tasks-panel` - flex 布局容器，默认 `display: none`，`.active` 时 `display: flex`
- `.all-tasks-toolbar` - 顶部工具栏，包含搜索框和筛选控件
- `.all-tasks-search` - 搜索输入框，`flex: 1; min-width: 200px`
- `.filter-group` / `.filter-select` / `.filter-date-input` - 筛选控件组
- `.all-tasks-table` - 表格布局，sticky header
- `.all-tasks-table th` - 可点击排序的表头，hover 变色
- `.all-tasks-table th.sorted` - 当前排序列高亮
- `.table-task-title:hover` - 可点击跳转到 detail view
- `.table-priority.p0/.p1/.p2/.p3` - 优先级颜色编码
- `.batch-actions` / `.batch-info` - 批量操作区域
- 响应式 `@media (max-width: 768px)` 适配

**Step 3: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): add All Tasks panel skeleton with toolbar and table"
```

---

## Task 4: 前端 - Tab 切换逻辑适配

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JavaScript 部分)

**Step 1: 修改 `switchTab()` 处理 `all_tasks` 特殊标签**

```javascript
function switchTab(status) {
    activeTab = status;
    if (currentView === 'detail') { closeTaskDetail(); }

    // Update nav items
    document.querySelectorAll('.nav-item[data-tab]').forEach(function(item) {
        item.classList.toggle('active', item.dataset.tab === status);
    });

    // Hide special pages
    document.getElementById('guidePage').classList.remove('active');
    document.getElementById('usagePage').classList.remove('active');
    document.getElementById('detailView').classList.remove('active');
    document.getElementById('allTasksPanel').classList.remove('active');
    guideVisible = false;
    usageVisible = false;

    if (status === 'all_tasks') {
        // Show All Tasks panel, hide normal list
        document.getElementById('contentPanel').style.display = 'none';
        document.getElementById('topBar').style.display = 'none';
        document.getElementById('allTasksPanel').classList.add('active');
        renderAllTasksTable();
    } else {
        // Show normal status-filtered task list
        document.getElementById('contentPanel').style.display = '';
        document.getElementById('topBar').style.display = '';
        document.getElementById('currentTabTitle').textContent = STATUS_LABELS[status] || status;
        document.getElementById('titleDot').style.background = STATUS_COLORS[status] || '#888';
        renderTaskList();
    }
}
```

**Step 2: 修改 `fetchTasks()` 在 all_tasks 模式下刷新表格**

```javascript
async function fetchTasks() {
    var data = await api('/tasks');
    if (data !== null) {
        allTasks = data;
        if (currentView === 'list') {
            if (activeTab === 'all_tasks') {
                renderAllTasksTable();
            } else {
                renderTaskList();
            }
        }
        updateNavCounts();
        fetchStatus();
    }
}
```

**Step 3: 确保 Guide/Usage 页面关闭时也隐藏 allTasksPanel**

在 `toggleGuidePage()` 和 `toggleUsagePage()` 中加入：

```javascript
document.getElementById('allTasksPanel').classList.remove('active');
```

**Step 4: 浏览器手动验证**

- 点击 "All Tasks" → 显示管理面板，隐藏顶栏和卡片列表
- 点击其他状态 tab → 恢复正常视图，隐藏管理面板
- 切换 Guide/Usage 页面不会导致面板残留

**Step 5: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): integrate All Tasks panel with tab switching logic"
```

---

## Task 5: 前端 - 筛选、排序、搜索逻辑

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JavaScript 部分)

**Step 1: 实现核心筛选/排序状态和函数**

```javascript
// State
var allTasksSortField = 'created_at';
var allTasksSortDir = 'desc';
var selectedTaskIds = new Set();

function getFilteredAllTasks() {
    var searchText = (document.getElementById('allTasksSearch').value || '').toLowerCase().trim();
    var statusFilter = document.getElementById('allTasksStatusFilter').value;
    var priorityFilter = document.getElementById('allTasksPriorityFilter').value;
    var dateFilter = document.getElementById('allTasksDateFilter').value;

    var filtered = allTasks.filter(function(task) {
        // Search: match id, title, prompt
        if (searchText) {
            var matchId = task.id.toLowerCase().indexOf(searchText) >= 0;
            var matchTitle = task.title.toLowerCase().indexOf(searchText) >= 0;
            var matchPrompt = (task.prompt || '').toLowerCase().indexOf(searchText) >= 0;
            if (!matchId && !matchTitle && !matchPrompt) return false;
        }
        // Status
        if (statusFilter && task.status !== statusFilter) return false;
        // Priority
        if (priorityFilter !== '' && String(task.priority) !== priorityFilter) return false;
        // Date (match YYYY-MM-DD prefix)
        if (dateFilter && task.created_at) {
            var taskDate = task.created_at.substring(0, 10);
            if (taskDate !== dateFilter) return false;
        }
        return true;
    });

    // Sort
    filtered.sort(function(a, b) {
        var valA, valB;
        switch (allTasksSortField) {
            case 'id':       valA = a.id; valB = b.id; break;
            case 'title':    valA = a.title.toLowerCase(); valB = b.title.toLowerCase(); break;
            case 'status':   valA = STATUSES.indexOf(a.status); valB = STATUSES.indexOf(b.status); break;
            case 'priority': valA = a.priority || 0; valB = b.priority || 0; break;
            case 'created_at': valA = a.created_at || ''; valB = b.created_at || ''; break;
            default: valA = a.created_at || ''; valB = b.created_at || '';
        }
        var cmp = valA < valB ? -1 : (valA > valB ? 1 : 0);
        return allTasksSortDir === 'asc' ? cmp : -cmp;
    });

    return filtered;
}

function sortAllTasks(field) {
    if (allTasksSortField === field) {
        allTasksSortDir = allTasksSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        allTasksSortField = field;
        allTasksSortDir = field === 'priority' ? 'desc' : 'asc';
    }
    renderAllTasksTable();
}

function filterAllTasks() { renderAllTasksTable(); }

function clearAllFilters() {
    document.getElementById('allTasksSearch').value = '';
    document.getElementById('allTasksStatusFilter').value = '';
    document.getElementById('allTasksPriorityFilter').value = '';
    document.getElementById('allTasksDateFilter').value = '';
    renderAllTasksTable();
}
```

**Step 2: 浏览器手动验证**

- 输入搜索文本 → 实时过滤
- 切换 status/priority/date 下拉 → 立即筛选
- 点击列头 → 排序方向切换（箭头指示符变化）
- 点击 "Clear Filters" → 重置全部筛选

**Step 3: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): implement search, filter, sort logic for All Tasks"
```

---

## Task 6: 前端 - 表格渲染和交互

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JavaScript 部分)

**Step 1: 实现 `renderAllTasksTable()` 函数**

```javascript
function renderAllTasksTable() {
    var filtered = getFilteredAllTasks();
    var tbody = document.getElementById('allTasksBody');
    var emptyEl = document.getElementById('allTasksEmpty');
    var infoEl = document.getElementById('allTasksResultInfo');

    // Update sort column indicators
    document.querySelectorAll('.all-tasks-table th[data-sort]').forEach(function(th) {
        var field = th.dataset.sort;
        th.classList.toggle('sorted', field === allTasksSortField);
        var arrow = th.querySelector('.sort-arrow');
        arrow.textContent = field === allTasksSortField
            ? (allTasksSortDir === 'asc' ? '\u25B2' : '\u25BC')
            : '';
    });

    // Result info
    infoEl.textContent = filtered.length + ' / ' + allTasks.length + ' tasks';

    if (filtered.length === 0) {
        tbody.innerHTML = '';
        emptyEl.style.display = 'block';
        return;
    }
    emptyEl.style.display = 'none';

    tbody.innerHTML = filtered.map(function(task) {
        var isSelected = selectedTaskIds.has(task.id);
        var color = STATUS_COLORS[task.status] || '#888';
        var priorityClass = 'p' + (task.priority || 0);
        var priorityLabel = task.priority > 0 ? 'P' + task.priority : 'Normal';
        var createdDate = formatTime(task.created_at);

        return '<tr class="' + (isSelected ? 'selected' : '') + '">'
            + '<td class="task-checkbox-cell"><input type="checkbox" '
            + (isSelected ? 'checked' : '')
            + ' onchange="toggleTaskSelection(\'' + task.id + '\', this.checked)"></td>'
            + '<td class="table-task-id">' + escapeHtml(task.id) + '</td>'
            + '<td class="table-task-title" onclick="openTaskDetail(\'' + task.id + '\')">'
            + escapeHtml(task.title) + '</td>'
            + '<td><span class="table-status-badge" style="background:' + color + '18; color:' + color + ';">'
            + '<span class="status-dot" style="background:' + color + ';"></span>'
            + (STATUS_LABELS[task.status] || task.status) + '</span></td>'
            + '<td><span class="table-priority ' + priorityClass + '">' + priorityLabel + '</span></td>'
            + '<td class="table-date">' + createdDate + '</td>'
            + '<td class="table-actions">'
            + '<button class="btn btn-danger btn-small" onclick="deleteTask(new Event(\'click\'), \'' + task.id + '\')">Delete</button>'
            + '</td>'
            + '</tr>';
    }).join('');

    updateBatchUI();
}
```

**Step 2: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): implement All Tasks table rendering with status/priority badges"
```

---

## Task 7: 前端 - 批量选择和批量删除

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JavaScript 部分)

**Step 1: 实现批量选择/取消/删除函数**

```javascript
function toggleTaskSelection(taskId, checked) {
    if (checked) { selectedTaskIds.add(taskId); }
    else { selectedTaskIds.delete(taskId); }
    updateBatchUI();
    // Sync "select all" checkbox
    var filtered = getFilteredAllTasks();
    var allChecked = filtered.length > 0 && filtered.every(function(t) {
        return selectedTaskIds.has(t.id);
    });
    document.getElementById('selectAllCheckbox').checked = allChecked;
}

function toggleSelectAll(checkbox) {
    var filtered = getFilteredAllTasks();
    if (checkbox.checked) {
        filtered.forEach(function(t) { selectedTaskIds.add(t.id); });
    } else {
        filtered.forEach(function(t) { selectedTaskIds.delete(t.id); });
    }
    renderAllTasksTable();
}

function updateBatchUI() {
    var count = selectedTaskIds.size;
    var batchEl = document.getElementById('batchActions');
    var infoEl = document.getElementById('batchInfo');
    if (count > 0) {
        batchEl.style.display = 'flex';
        infoEl.textContent = count + ' selected';
    } else {
        batchEl.style.display = 'none';
    }
}

function clearSelection() {
    selectedTaskIds.clear();
    document.getElementById('selectAllCheckbox').checked = false;
    renderAllTasksTable();
}

async function batchDeleteTasks() {
    var ids = Array.from(selectedTaskIds);
    if (ids.length === 0) return;
    if (!confirm('Delete ' + ids.length + ' selected task(s)?')) return;

    var data = await api('/tasks/batch-delete', {
        method: 'POST',
        body: JSON.stringify({ task_ids: ids }),
    });
    if (data !== null) {
        showToast(data.count + ' task(s) deleted');
        selectedTaskIds.clear();
        document.getElementById('selectAllCheckbox').checked = false;
        fetchTasks();
    }
}
```

设计决策：
- "Select All" 仅选择**当前筛选结果**中的任务，非全量
- 删除操作需 `confirm()` 二次确认
- 删除成功后自动清空选择状态并刷新任务列表

**Step 2: 浏览器手动验证**

- 勾选单个任务 → 显示 "1 selected" 和批量操作按钮
- 勾选 "Select All" → 选中当前筛选结果内所有任务
- 点击 "Delete Selected" → 弹出确认框 → 确认后删除并刷新
- 点击 "Clear Selection" → 取消全部选中

**Step 3: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): implement batch selection and delete for All Tasks"
```

---

## Task 8: 前端 - 响应式适配

**Files:**
- Modify: `claude_flow/web/templates/index.html` (CSS 部分)

**Step 1: 添加移动端样式**

```css
@media (max-width: 768px) {
    .all-tasks-toolbar {
        padding: 12px 14px;
    }
    .all-tasks-toolbar-row {
        flex-direction: column;
        align-items: stretch;
    }
    .all-tasks-search {
        min-width: unset;
    }
    .all-tasks-table-wrapper {
        padding: 0 14px 14px;
    }
    .all-tasks-table td, .all-tasks-table th {
        padding: 8px 6px;
        font-size: 11px;
    }
    .table-task-title {
        max-width: 150px;
    }
}
```

**Step 2: 浏览器手动验证**

- 缩小窗口至 768px 以下 → 筛选控件垂直堆叠
- 表格文字缩小，标题列宽度受限并以省略号截断

**Step 3: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): add responsive styles for All Tasks panel"
```

---

## Task 9: 后端 API 测试补全

**Files:**
- Modify: `tests/test_web_api.py`

**Step 1: 验证批量删除对 running 任务的资源清理**

```python
def test_batch_delete_with_running_task(self, client, tm, web_app):
    """Batch delete should clean up resources for running tasks."""
    t1 = tm.add("T1", "P1")
    tm.update_status(t1.id, TaskStatus.RUNNING)
    t2 = tm.add("T2", "P2")
    resp = client.post(
        "/api/tasks/batch-delete",
        json={"task_ids": [t1.id, t2.id]},
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["data"]["count"] == 2
```

**Step 2: 运行完整测试套件**

Run: `pytest tests/test_web_api.py -v`
Expected: ALL PASSED

**Step 3: Commit**

```bash
git add tests/test_web_api.py
git commit -m "test(api): add batch delete edge case tests"
```

---

## Task 10: 最终集成验证

**Step 1: 运行完整测试套件**

Run: `pytest -v`
Expected: ALL PASSED

**Step 2: 启动 Web Manager 端到端验证**

```bash
cd <target-project>
cf web
```

验证清单：
- [ ] Sidebar 显示 "Management > All Tasks" 入口，badge 显示总任务数
- [ ] 点击 "All Tasks" 切换到管理面板，隐藏 top bar
- [ ] 搜索框实时筛选（按 ID / 标题 / prompt）
- [ ] 状态下拉筛选正常工作
- [ ] 优先级下拉筛选正常工作
- [ ] 日期选择器筛选正常工作
- [ ] 点击列头切换排序方向，排序箭头指示正确
- [ ] 默认按创建时间降序排序
- [ ] "Clear Filters" 重置所有筛选条件
- [ ] 单选/全选 checkbox 联动正确
- [ ] 批量删除弹出确认框，确认后执行删除
- [ ] 删除后列表自动刷新，sidebar 计数更新
- [ ] 点击任务标题跳转到 detail view
- [ ] 从 All Tasks 切换到其他 tab 后面板正确隐藏
- [ ] Guide/Usage 页面和 All Tasks 互不干扰
- [ ] 移动端响应式布局正确

**Step 3: 最终 Commit**

```bash
git add -A
git commit -m "feat: sidebar All Tasks management panel with search/filter/sort/batch-delete"
```

---

## 技术决策总结

| 决策 | 原因 |
|------|------|
| 前端纯内存筛选/排序 | 任务总量通常 < 1000，避免每次筛选调 API 的延迟 |
| 表格视图而非卡片 | 管理场景需要高信息密度和批量操作 |
| 全选仅作用于筛选结果 | 避免误删不可见任务 |
| 批量删除非事务性 | 简化实现，部分失败可通过 `failed` 数组告知用户 |
| 复用 `_cleanup_task_resources` | DRY，确保删除 running 任务时停止子进程 |
| 排序箭头 Unicode 字符 | 无需额外图标库，KISS |
