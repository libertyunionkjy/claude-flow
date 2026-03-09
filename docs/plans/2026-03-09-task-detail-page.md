# Task Detail Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a task detail page with hash-based routing, replacing modal-based交互为 Tab 内嵌视图，提供沉浸式的任务管理体验。

**Architecture:** 在现有 SPA 单 HTML 文件中实现 hash-based 路由（`/#/` = 看板，`/#/task/<id>` = 详情页）。详情页采用左侧信息面板 + 右侧 Tab 切换的布局。Chat/Plan/Log 功能从模态框迁移为 Tab 内嵌渲染，同时保留看板上的模态框快捷入口。

**Tech Stack:** Vanilla JS (hash routing), CSS Grid/Flexbox, 现有 API 接口

---

## Overview

### Page Layout

```
+-------------------------------------------------------------+
| <- Back to Board    task-a1b2c3    [Status Badge]            |
| Task Title                                                   |
+-------------------------------------------------------------+
| +----------------+ +--------------------------------------+ |
| | Info Panel     | | [Overview] [Plan] [Chat] [Log]       | |
| |                | |                                      | |
| | Status: planned| | +----------------------------------+ | |
| | Priority: P2   | | |                                  | | |
| | Branch: cf/... | | |   Active Tab Content             | | |
| | Created: 03-09 | | |                                  | | |
| |                | | |                                  | | |
| | -- Actions --  | | |                                  | | |
| | [Plan]         | | +----------------------------------+ | |
| | [Approve]      | |                                      | |
| | [Run]          | |                                      | |
| | [Reset]        | |                                      | |
| | [Delete]       | |                                      | |
| +----------------+ +--------------------------------------+ |
+-------------------------------------------------------------+
```

### Route Table

| Hash | View | Description |
|------|------|-------------|
| `#/` or empty | Board View | 现有看板（任务列表 + 侧栏导航） |
| `#/task/<task_id>` | Detail View | 任务详情页 |

### Tab Definitions

| Tab | Content | Notes |
|-----|---------|-------|
| Overview | Prompt 全文、元数据、时间线 | 默认 Tab |
| Plan | Plan 文档（Markdown 渲染） | planned 状态显示 Approve 按钮 |
| Chat | 交互式聊天界面 | 复用现有 chat 逻辑 |
| Log | 执行日志 | running 时自动刷新 |

---

## Task 1: Hash Router + View Container

**Files:**
- Modify: `claude_flow/web/templates/index.html` (CSS section ~line 7-741)
- Modify: `claude_flow/web/templates/index.html` (HTML body ~line 743-1008)
- Modify: `claude_flow/web/templates/index.html` (JS section ~line 1104-1957)

### Step 1: Add detail page CSS styles

在 `</style>` 标签前（line 741 之前）添加以下 CSS：

```css
/* -- Task Detail Page ------------------------------------------------ */
.detail-page {
    display: none;
    flex-direction: column;
    height: 100%;
}
.detail-page.active { display: flex; }

.detail-top-bar {
    background: #12122a;
    border-bottom: 1px solid #2a2a4a;
    padding: 14px 24px;
    display: flex;
    align-items: center;
    gap: 14px;
    flex-shrink: 0;
}

.detail-back-btn {
    background: transparent;
    border: 1px solid #2a2a4a;
    color: #8888aa;
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
    font-family: inherit;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    gap: 6px;
}
.detail-back-btn:hover { border-color: #00d4ff; color: #00d4ff; }

.detail-task-id {
    font-family: "SF Mono", "Fira Code", monospace;
    font-size: 12px;
    color: #5a6a8a;
}

.detail-task-title {
    font-size: 18px;
    font-weight: 600;
    color: #e0e0e0;
    flex: 1;
}

.detail-status-badge {
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.detail-body {
    display: flex;
    flex: 1;
    overflow: hidden;
}

/* -- Detail Sidebar (Info Panel) ------------------------------------- */
.detail-sidebar {
    width: 260px;
    min-width: 260px;
    background: #12122a;
    border-right: 1px solid #2a2a4a;
    padding: 20px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 16px;
}

.detail-info-section h3 {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: #4a4a6a;
    margin-bottom: 10px;
}

.detail-info-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 0;
    font-size: 13px;
}

.detail-info-label { color: #6c7a9a; }
.detail-info-value { color: #d0d0e0; font-weight: 500; }
.detail-info-value.mono {
    font-family: "SF Mono", "Fira Code", monospace;
    font-size: 11px;
}

.detail-actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.detail-actions .btn { width: 100%; text-align: center; }

.detail-prompt-preview {
    background: #0f0f23;
    border-radius: 6px;
    padding: 10px 12px;
    font-size: 12px;
    color: #8888aa;
    max-height: 120px;
    overflow-y: auto;
    white-space: pre-wrap;
    font-family: "SF Mono", "Fira Code", monospace;
    line-height: 1.5;
}

/* -- Detail Main (Tab Area) ------------------------------------------ */
.detail-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.detail-tabs {
    display: flex;
    border-bottom: 1px solid #2a2a4a;
    background: #12122a;
    padding: 0 20px;
    flex-shrink: 0;
}

.detail-tab {
    padding: 12px 18px;
    font-size: 13px;
    font-weight: 500;
    color: #6c7a9a;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    font-family: inherit;
    transition: all 0.15s ease;
}
.detail-tab:hover { color: #b0b0cc; }
.detail-tab.active { color: #00d4ff; border-bottom-color: #00d4ff; }

.detail-tab-content {
    flex: 1;
    overflow-y: auto;
    padding: 20px 24px;
}

/* -- Detail Overview Tab --------------------------------------------- */
.detail-overview-prompt {
    background: #0f0f23;
    border: 1px solid #2a2a4a;
    border-radius: 8px;
    padding: 16px;
    font-size: 13px;
    line-height: 1.7;
    color: #d0d0e0;
    white-space: pre-wrap;
    font-family: "SF Mono", "Fira Code", monospace;
}

.detail-timeline {
    margin-top: 20px;
}

.detail-timeline h4 {
    font-size: 14px;
    color: #8888aa;
    margin-bottom: 12px;
    font-weight: 600;
}

.timeline-item {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 8px 0;
    font-size: 13px;
    position: relative;
}

.timeline-item::before {
    content: '';
    position: absolute;
    left: 5px;
    top: 24px;
    bottom: -8px;
    width: 1px;
    background: #2a2a4a;
}

.timeline-item:last-child::before { display: none; }

.timeline-dot {
    width: 11px;
    height: 11px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-top: 3px;
}

.timeline-label { color: #6c7a9a; min-width: 80px; }
.timeline-time { color: #d0d0e0; }

/* -- Detail Chat Tab (inline) ---------------------------------------- */
.detail-chat-container {
    display: flex;
    flex-direction: column;
    height: 100%;
}

.detail-chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    background: #0a0a16;
    border: 1px solid #2a2a4a;
    border-radius: 8px;
    margin-bottom: 12px;
}

.detail-chat-actions {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
}

.detail-chat-input-area {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
}

.detail-chat-input-area textarea {
    flex: 1;
    padding: 10px 12px;
    background: #0f0f23;
    border: 1px solid #2a2a4a;
    border-radius: 8px;
    color: #e0e0e0;
    font-size: 14px;
    font-family: inherit;
    resize: none;
    min-height: 44px;
    max-height: 120px;
}

.detail-chat-input-area textarea:focus { outline: none; border-color: #00d4ff; }
.detail-chat-input-area button { align-self: flex-end; }

.detail-chat-typing {
    color: #6688aa;
    font-size: 12px;
    font-style: italic;
    padding: 4px 14px;
    display: none;
}

/* -- Detail Plan Tab ------------------------------------------------- */
.detail-plan-actions {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
}

/* -- Detail Log Tab -------------------------------------------------- */
.detail-log-actions {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
}

/* -- Mobile Detail Page ---------------------------------------------- */
@media (max-width: 768px) {
    .detail-top-bar {
        padding: 10px 14px;
        flex-wrap: wrap;
        gap: 8px;
    }

    .detail-task-title { font-size: 15px; }

    .detail-body {
        flex-direction: column;
    }

    .detail-sidebar {
        width: 100%;
        min-width: 100%;
        border-right: none;
        border-bottom: 1px solid #2a2a4a;
        max-height: 200px;
        padding: 14px;
    }

    .detail-actions {
        flex-direction: row;
        flex-wrap: wrap;
    }

    .detail-actions .btn {
        width: auto;
        flex: 1;
        min-width: 80px;
    }

    .detail-tabs { padding: 0 10px; overflow-x: auto; }
    .detail-tab { padding: 10px 14px; font-size: 12px; white-space: nowrap; }
    .detail-tab-content { padding: 14px; }
}
```

### Step 2: Add detail page HTML container

在 `<!-- Guide Page -->` 容器之后（line 1008 `</div>` 之后），添加详情页 HTML 容器：

```html
<!-- Task Detail Page -->
<div class="detail-page" id="detailPage">
    <div class="detail-top-bar">
        <button class="detail-back-btn" onclick="navigateToBoard()">&#8592; Back</button>
        <span class="detail-task-id" id="detailTaskId"></span>
        <span class="detail-task-title" id="detailTaskTitle"></span>
        <span class="detail-status-badge" id="detailStatusBadge"></span>
    </div>
    <div class="detail-body">
        <div class="detail-sidebar">
            <div class="detail-info-section">
                <h3>Information</h3>
                <div id="detailInfoRows"></div>
            </div>
            <div class="detail-info-section">
                <h3>Prompt Preview</h3>
                <div class="detail-prompt-preview" id="detailPromptPreview"></div>
            </div>
            <div class="detail-info-section">
                <h3>Actions</h3>
                <div class="detail-actions" id="detailActions"></div>
            </div>
        </div>
        <div class="detail-main">
            <div class="detail-tabs" id="detailTabs"></div>
            <div class="detail-tab-content" id="detailTabContent"></div>
        </div>
    </div>
</div>
```

### Step 3: Add hash router in JS

在 JS 的 `/* -- Init */` 部分之前（line 1953 之前），添加路由逻辑：

```javascript
/* -- Router ---------------------------------------------------------- */
let currentView = 'board';  // 'board' or 'detail'
let detailTaskId = null;
let detailActiveTab = 'overview';

function initRouter() {
    window.addEventListener('hashchange', handleRoute);
    handleRoute();
}

function handleRoute() {
    var hash = window.location.hash || '#/';
    var taskMatch = hash.match(/^#\/task\/(.+)$/);

    if (taskMatch) {
        showDetailView(taskMatch[1]);
    } else {
        showBoardView();
    }
}

function navigateToBoard() {
    window.location.hash = '#/';
}

function navigateToTask(taskId) {
    window.location.hash = '#/task/' + taskId;
}

function showBoardView() {
    currentView = 'board';
    detailTaskId = null;
    document.getElementById('sidebar').style.display = '';
    document.getElementById('topBar').style.display = '';
    document.getElementById('contentPanel').style.display = '';
    document.getElementById('detailPage').classList.remove('active');
    // Restore guide page state
    if (guideVisible) {
        document.getElementById('guidePage').classList.add('active');
        document.getElementById('contentPanel').style.display = 'none';
        document.getElementById('topBar').style.display = 'none';
    }
    renderTaskList();
}

function showDetailView(taskId) {
    currentView = 'detail';
    detailTaskId = taskId;
    detailActiveTab = 'overview';

    // Hide board elements
    document.getElementById('sidebar').style.display = 'none';
    document.getElementById('topBar').style.display = 'none';
    document.getElementById('contentPanel').style.display = 'none';
    document.getElementById('guidePage').classList.remove('active');

    // Show detail page
    document.getElementById('detailPage').classList.add('active');

    // Render with current data or fetch
    var task = allTasks.find(function(t) { return t.id === taskId; });
    if (task) {
        renderDetailPage(task);
    } else {
        // Fetch fresh then render
        fetchTaskAndRenderDetail(taskId);
    }
}

async function fetchTaskAndRenderDetail(taskId) {
    var data = await api('/tasks/' + taskId);
    if (data) {
        renderDetailPage(data);
    } else {
        showToast('Task ' + taskId + ' not found', true);
        navigateToBoard();
    }
}
```

### Step 4: Update Init section

替换 Init 部分（line 1953-1956）：

```javascript
/* -- Init ------------------------------------------------------------ */
switchTab(activeTab);
fetchTasks();
startAutoRefresh();
initRouter();
```

### Step 5: Add "Open" entry point on cards

修改 `renderCard` 函数（line 1248-1309），在 `card-header` 中的 `card-title` 后添加 Open 按钮：

在 card-header 的 `card-meta` div 内，`metaHtml` 之前插入一个 open 按钮：

```javascript
// 在 card-header 的 metaHtml 构建处添加 open 按钮
var openBtn = '<button class="btn btn-outline btn-small" onclick="event.stopPropagation(); navigateToTask(\'' + task.id + '\')" style="margin-left:4px;">Open</button>';
```

修改 return 语句，在 `card-meta` 之前插入 openBtn：

```javascript
return '<div class="card" onclick="toggleCard(\'' + task.id + '\')">'
    + '<div class="card-header">'
    + '<span class="card-id">' + escapeHtml(task.id) + '</span>'
    + '<span class="card-title">' + escapeHtml(task.title) + '</span>'
    + '<div class="card-meta">' + openBtn + metaHtml + '</div>'
    + '</div>'
    + statusIndicator
    + '<div class="card-details ' + detailClass + '">'
    + detailHtml
    + actionsHtml
    + '</div>'
    + '</div>';
```

---

## Task 2: Detail Page Rendering (Info Panel + Overview Tab)

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JS section)

### Step 1: Implement renderDetailPage function

在 Router 部分之后添加：

```javascript
/* -- Detail Page Rendering ------------------------------------------- */
function renderDetailPage(task) {
    // Top bar
    document.getElementById('detailTaskId').textContent = task.id;
    document.getElementById('detailTaskTitle').textContent = task.title;

    var badge = document.getElementById('detailStatusBadge');
    badge.textContent = STATUS_LABELS[task.status] || task.status;
    badge.style.background = STATUS_COLORS[task.status] || '#444';
    badge.style.color = ['running', 'pending'].includes(task.status) ? '#0f0f23' : '#fff';

    // Info rows
    renderDetailInfo(task);

    // Prompt preview
    document.getElementById('detailPromptPreview').textContent =
        (task.prompt || '').substring(0, 200) + (task.prompt && task.prompt.length > 200 ? '...' : '');

    // Actions
    renderDetailActions(task);

    // Tabs
    renderDetailTabs(task);

    // Default tab content
    renderDetailTabContent(task, detailActiveTab);
}

function renderDetailInfo(task) {
    var rows = '';
    rows += infoRow('Status', STATUS_LABELS[task.status] || task.status);
    if (task.priority > 0) rows += infoRow('Priority', 'P' + task.priority);
    if (task.branch) rows += infoRow('Branch', '<span class="mono">' + escapeHtml(task.branch) + '</span>');
    if (task.worker_id !== null && task.worker_id !== undefined) rows += infoRow('Worker', 'W' + task.worker_id);
    if (task.plan_mode) rows += infoRow('Plan Mode', task.plan_mode);
    rows += infoRow('Created', formatTime(task.created_at));
    if (task.started_at) rows += infoRow('Started', formatTime(task.started_at));
    if (task.completed_at) rows += infoRow('Completed', formatTime(task.completed_at));
    if (task.error) rows += infoRow('Error', '<span style="color:#ff6b6b;">' + escapeHtml(task.error) + '</span>');
    document.getElementById('detailInfoRows').innerHTML = rows;
}

function infoRow(label, value) {
    return '<div class="detail-info-row">'
        + '<span class="detail-info-label">' + label + '</span>'
        + '<span class="detail-info-value">' + value + '</span>'
        + '</div>';
}

function renderDetailActions(task) {
    var btns = [];
    var s = task.status;

    // Plan actions
    if (s === 'pending') {
        btns.push('<button class="btn btn-info" onclick="detailPlanAuto()">Auto Plan</button>');
        btns.push('<button class="btn btn-warning" onclick="detailPlanInteractive()">Chat Plan</button>');
    }
    if (task.plan_file) {
        btns.push('<button class="btn btn-outline" onclick="detailSwitchTab(\'plan\')">View Plan</button>');
    }

    // Approve
    if (s === 'planned') {
        btns.push('<button class="btn btn-success" onclick="detailApprove()">Approve</button>');
    }

    // Run
    if (s === 'approved') {
        btns.push('<button class="btn btn-warning" onclick="detailRun()">Run</button>');
    }

    // Chat (for planning/planned states)
    if (s === 'planning' && task.plan_mode === 'interactive') {
        btns.push('<button class="btn btn-warning" onclick="detailSwitchTab(\'chat\')">Open Chat</button>');
    }
    if (s === 'planned') {
        btns.push('<button class="btn btn-outline" onclick="detailSwitchTab(\'chat\')">Chat</button>');
    }

    // Log
    if (['running', 'done', 'failed'].includes(s)) {
        btns.push('<button class="btn btn-outline" onclick="detailSwitchTab(\'log\')">View Log</button>');
    }

    // Respond
    if (s === 'needs_input') {
        btns.push('<button class="btn btn-warning" onclick="openRespondModal(null, \'' + task.id + '\')">Respond</button>');
    }

    // Reset
    if (['failed', 'needs_input', 'running'].includes(s)) {
        btns.push('<button class="btn btn-outline" onclick="detailReset()">Reset</button>');
    }

    // Retry
    if (s === 'failed') {
        btns.push('<button class="btn btn-warning" onclick="detailRetry()">Retry</button>');
    }

    // Delete
    if (!['running'].includes(s)) {
        btns.push('<button class="btn btn-danger" onclick="detailDelete()">Delete</button>');
    }

    document.getElementById('detailActions').innerHTML = btns.join('');
}
```

### Step 2: Implement Tab rendering

```javascript
function renderDetailTabs(task) {
    var tabs = [
        { id: 'overview', label: 'Overview' },
        { id: 'plan', label: 'Plan' },
        { id: 'chat', label: 'Chat' },
        { id: 'log', label: 'Log' },
    ];

    var html = tabs.map(function(tab) {
        var activeClass = tab.id === detailActiveTab ? ' active' : '';
        return '<button class="detail-tab' + activeClass + '" onclick="detailSwitchTab(\'' + tab.id + '\')">' + tab.label + '</button>';
    }).join('');

    document.getElementById('detailTabs').innerHTML = html;
}

function detailSwitchTab(tabId) {
    detailActiveTab = tabId;
    var task = allTasks.find(function(t) { return t.id === detailTaskId; });
    if (!task) return;

    // Update tab active state
    document.querySelectorAll('.detail-tab').forEach(function(el) {
        el.classList.toggle('active', el.textContent.toLowerCase() === tabId);
    });

    renderDetailTabContent(task, tabId);
}

function renderDetailTabContent(task, tabId) {
    var container = document.getElementById('detailTabContent');

    switch (tabId) {
        case 'overview': renderOverviewTab(task, container); break;
        case 'plan':     renderPlanTab(task, container); break;
        case 'chat':     renderChatTab(task, container); break;
        case 'log':      renderLogTab(task, container); break;
        default:         renderOverviewTab(task, container); break;
    }
}
```

### Step 3: Implement Overview tab

```javascript
function renderOverviewTab(task, container) {
    var html = '<h4 style="font-size:14px; color:#8888aa; margin-bottom:12px;">Prompt</h4>';
    html += '<div class="detail-overview-prompt">' + escapeHtml(task.prompt || 'No prompt') + '</div>';

    // Timeline
    html += '<div class="detail-timeline"><h4>Timeline</h4>';

    var events = [];
    if (task.created_at) events.push({ label: 'Created', time: task.created_at, color: '#6c5ce7' });
    if (task.started_at) events.push({ label: 'Started', time: task.started_at, color: '#fdcb6e' });
    if (task.completed_at) events.push({ label: 'Completed', time: task.completed_at, color: task.status === 'done' ? '#00b894' : '#ff6b6b' });

    if (events.length === 0) {
        html += '<p style="color:#4a4a6a; font-size:13px;">No timeline events yet.</p>';
    } else {
        events.forEach(function(ev) {
            html += '<div class="timeline-item">'
                + '<span class="timeline-dot" style="background:' + ev.color + ';"></span>'
                + '<span class="timeline-label">' + ev.label + '</span>'
                + '<span class="timeline-time">' + formatTime(ev.time) + '</span>'
                + '</div>';
        });
    }

    html += '</div>';

    // Error section
    if (task.error) {
        html += '<div style="margin-top:20px;">'
            + '<h4 style="font-size:14px; color:#ff6b6b; margin-bottom:8px;">Error</h4>'
            + '<div style="background:#1a0a0a; border:1px solid #3a1a1a; border-radius:8px; padding:12px; font-size:13px; color:#ff9090; white-space:pre-wrap;">'
            + escapeHtml(task.error)
            + '</div></div>';
    }

    container.innerHTML = html;
}
```

---

## Task 3: Plan Tab (Inline)

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JS section)

### Step 1: Implement Plan tab rendering

```javascript
async function renderPlanTab(task, container) {
    var html = '';

    // Actions bar
    if (task.status === 'planned') {
        html += '<div class="detail-plan-actions">'
            + '<button class="btn btn-success btn-small" onclick="detailApprove()">Approve Plan</button>'
            + '<button class="btn btn-outline btn-small" onclick="detailSwitchTab(\'chat\')">Discuss in Chat</button>'
            + '</div>';
    }

    html += '<div class="plan-content" id="detailPlanContent" style="max-height:none;">Loading...</div>';
    container.innerHTML = html;

    // Fetch plan content
    if (task.plan_file || ['planned', 'approved', 'running', 'done', 'failed'].includes(task.status)) {
        var data = await api('/tasks/' + task.id + '/plan');
        var planEl = document.getElementById('detailPlanContent');
        if (planEl) {
            if (data !== null) {
                planEl.innerHTML = renderMarkdown(data.content);
            } else {
                planEl.innerHTML = '<p style="color:#4a4a6a;">No plan document available. Use <strong>Auto Plan</strong> or <strong>Chat Plan</strong> to generate one.</p>';
            }
        }
    } else {
        var planEl = document.getElementById('detailPlanContent');
        if (planEl) {
            planEl.innerHTML = '<p style="color:#4a4a6a;">No plan yet. Go to Actions and choose a planning mode.</p>';
        }
    }
}
```

---

## Task 4: Chat Tab (Inline)

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JS section)

### Step 1: Implement Chat tab rendering

```javascript
let detailChatPollingTimer = null;

async function renderChatTab(task, container) {
    // Stop any existing polling
    stopDetailChatPolling();

    var html = '<div class="detail-chat-container">';
    html += '<div class="detail-chat-actions">';
    html += '<span style="font-size:13px; color:#6c7a9a;">Chat with AI about this task</span>';
    html += '<button class="btn btn-success btn-small" id="detailChatFinalizeBtn" onclick="detailFinalizeChat()">Generate Plan</button>';
    html += '</div>';
    html += '<div class="detail-chat-messages" id="detailChatMessages"></div>';
    html += '<div class="detail-chat-typing" id="detailChatTyping">AI is thinking...</div>';
    html += '<div class="detail-chat-input-area">';
    html += '<textarea id="detailChatInput" placeholder="Describe requirements, ask questions, or refine the plan..." rows="2"'
        + ' onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();sendDetailChatMessage();}"></textarea>';
    html += '<button class="btn btn-primary" onclick="sendDetailChatMessage()" id="detailChatSendBtn">Send</button>';
    html += '</div>';
    html += '</div>';
    container.innerHTML = html;

    // Load existing messages
    var data = await api('/tasks/' + task.id + '/chat');
    if (data !== null && data.exists && data.messages && data.messages.length > 0) {
        renderDetailChatMessages(data.messages);
    }

    // If AI is thinking, start polling
    if (data !== null && data.thinking) {
        document.getElementById('detailChatSendBtn').disabled = true;
        document.getElementById('detailChatInput').disabled = true;
        document.getElementById('detailChatTyping').style.display = 'block';
        startDetailChatPolling(task.id);
    }

    // Focus input
    var input = document.getElementById('detailChatInput');
    if (input) input.focus();
}

function renderDetailChatMessages(messages) {
    var container = document.getElementById('detailChatMessages');
    if (!container) return;
    container.innerHTML = '';
    messages.forEach(function(msg) {
        var bubble = document.createElement('div');
        bubble.className = 'chat-bubble ' + msg.role;
        var roleLabel = msg.role === 'user' ? 'You' : 'AI';
        bubble.innerHTML = '<div class="chat-role">' + roleLabel + '</div>'
            + renderMarkdown(msg.content);
        container.appendChild(bubble);
    });
    container.scrollTop = container.scrollHeight;
}

async function sendDetailChatMessage() {
    if (!detailTaskId) return;
    var input = document.getElementById('detailChatInput');
    var message = input.value.trim();
    if (!message) return;

    // Add user bubble immediately
    var container = document.getElementById('detailChatMessages');
    var userBubble = document.createElement('div');
    userBubble.className = 'chat-bubble user';
    userBubble.innerHTML = '<div class="chat-role">You</div>' + renderMarkdown(message);
    container.appendChild(userBubble);
    container.scrollTop = container.scrollHeight;

    // Disable input
    input.value = '';
    document.getElementById('detailChatSendBtn').disabled = true;
    document.getElementById('detailChatInput').disabled = true;
    document.getElementById('detailChatTyping').style.display = 'block';

    // Send to API
    var data = await api('/tasks/' + detailTaskId + '/chat', {
        method: 'POST',
        body: JSON.stringify({ message: message }),
    });

    if (data !== null && data.accepted) {
        startDetailChatPolling(detailTaskId);
    } else {
        document.getElementById('detailChatSendBtn').disabled = false;
        document.getElementById('detailChatInput').disabled = false;
        document.getElementById('detailChatTyping').style.display = 'none';
    }
}

function startDetailChatPolling(taskId) {
    stopDetailChatPolling();
    detailChatPollingTimer = setInterval(async function() {
        var data = await api('/tasks/' + taskId + '/chat');
        if (data === null) { stopDetailChatPolling(); return; }
        if (!data.thinking) {
            stopDetailChatPolling();
            var sendBtn = document.getElementById('detailChatSendBtn');
            var chatInput = document.getElementById('detailChatInput');
            var typing = document.getElementById('detailChatTyping');
            if (sendBtn) sendBtn.disabled = false;
            if (chatInput) { chatInput.disabled = false; chatInput.focus(); }
            if (typing) typing.style.display = 'none';

            if (data.messages && data.messages.length > 0) {
                var lastMsg = data.messages[data.messages.length - 1];
                if (lastMsg.role === 'assistant') {
                    var container = document.getElementById('detailChatMessages');
                    if (container) {
                        var aiBubble = document.createElement('div');
                        aiBubble.className = 'chat-bubble assistant';
                        aiBubble.innerHTML = '<div class="chat-role">AI</div>' + renderMarkdown(lastMsg.content);
                        container.appendChild(aiBubble);
                        container.scrollTop = container.scrollHeight;
                    }
                }
            }
        }
    }, 1500);
}

function stopDetailChatPolling() {
    if (detailChatPollingTimer) { clearInterval(detailChatPollingTimer); detailChatPollingTimer = null; }
}

async function detailFinalizeChat() {
    if (!detailTaskId) return;
    var data = await api('/tasks/' + detailTaskId + '/chat/finalize', { method: 'POST' });
    if (data !== null) {
        showToast('Plan generation started from chat...');
        fetchTasks();
    }
}
```

---

## Task 5: Log Tab (Inline)

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JS section)

### Step 1: Implement Log tab rendering

```javascript
let detailLogAutoRefresh = null;

async function renderLogTab(task, container) {
    // Stop previous auto-refresh
    stopDetailLogRefresh();

    var html = '<div class="detail-log-actions">';
    html += '<button class="btn btn-outline btn-small" onclick="refreshDetailLog()">Refresh</button>';
    if (task.status === 'running') {
        html += '<span style="font-size:12px; color:#fdcb6e; display:flex; align-items:center; gap:4px;">'
            + '<span class="spinner"></span>Auto-refreshing every 3s</span>';
    }
    html += '</div>';
    html += '<div class="log-content" id="detailLogContent" style="max-height:none;">Loading...</div>';
    container.innerHTML = html;

    await loadDetailLog(task.id);

    // Auto-refresh for running tasks
    if (task.status === 'running') {
        detailLogAutoRefresh = setInterval(function() {
            if (detailTaskId) loadDetailLog(detailTaskId);
        }, 3000);
    }
}

async function loadDetailLog(taskId) {
    var data = await api('/tasks/' + taskId + '/log');
    var el = document.getElementById('detailLogContent');
    if (!el) return;
    if (data === null || !data.exists) {
        el.textContent = 'No log data available.';
        return;
    }
    if (data.structured && data.data) {
        el.innerHTML = renderStructuredLog(data.data);
    } else if (data.content) {
        el.textContent = data.content;
    } else {
        el.textContent = 'No log data available.';
    }
}

async function refreshDetailLog() {
    if (detailTaskId) {
        await loadDetailLog(detailTaskId);
        showToast('Log refreshed');
    }
}

function stopDetailLogRefresh() {
    if (detailLogAutoRefresh) { clearInterval(detailLogAutoRefresh); detailLogAutoRefresh = null; }
}
```

---

## Task 6: Detail Page Action Handlers

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JS section)

### Step 1: Implement detail action functions

```javascript
/* -- Detail Page Actions --------------------------------------------- */
async function detailPlanAuto() {
    if (!detailTaskId) return;
    var data = await api('/tasks/' + detailTaskId + '/plan', {
        method: 'POST',
        body: JSON.stringify({ mode: 'auto' }),
    });
    if (data !== null) {
        showToast('Auto plan generation started');
        fetchTasks();
        // Refresh detail view after short delay
        setTimeout(function() { refreshDetailView(); }, 1000);
    }
}

async function detailPlanInteractive() {
    if (!detailTaskId) return;
    var data = await api('/tasks/' + detailTaskId + '/plan', {
        method: 'POST',
        body: JSON.stringify({ mode: 'interactive' }),
    });
    if (data !== null) {
        showToast('Interactive planning started');
        fetchTasks();
        detailSwitchTab('chat');
    }
}

async function detailApprove() {
    if (!detailTaskId) return;
    var data = await api('/tasks/' + detailTaskId + '/approve', { method: 'POST' });
    if (data !== null) {
        showToast('Task approved');
        fetchTasks();
        setTimeout(function() { refreshDetailView(); }, 500);
    }
}

async function detailRun() {
    if (!detailTaskId) return;
    var data = await api('/tasks/' + detailTaskId + '/run', { method: 'POST' });
    if (data !== null) {
        showToast('Task execution started');
        fetchTasks();
        setTimeout(function() { refreshDetailView(); detailSwitchTab('log'); }, 1000);
    }
}

async function detailReset() {
    if (!detailTaskId) return;
    var data = await api('/tasks/' + detailTaskId + '/reset', { method: 'POST' });
    if (data !== null) {
        showToast('Task reset');
        fetchTasks();
        setTimeout(function() { refreshDetailView(); }, 500);
    }
}

async function detailRetry() {
    if (!detailTaskId) return;
    var data = await api('/tasks/' + detailTaskId, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'approved' }),
    });
    if (data !== null) {
        showToast('Task set to approved for retry');
        fetchTasks();
        setTimeout(function() { refreshDetailView(); }, 500);
    }
}

async function detailDelete() {
    if (!detailTaskId) return;
    if (!confirm('Delete task ' + detailTaskId + '?')) return;
    var data = await api('/tasks/' + detailTaskId, { method: 'DELETE' });
    if (data !== null) {
        showToast('Task deleted');
        fetchTasks();
        navigateToBoard();
    }
}

function refreshDetailView() {
    if (currentView !== 'detail' || !detailTaskId) return;
    var task = allTasks.find(function(t) { return t.id === detailTaskId; });
    if (task) {
        renderDetailPage(task);
    } else {
        fetchTaskAndRenderDetail(detailTaskId);
    }
}
```

---

## Task 7: Auto-Refresh Integration

**Files:**
- Modify: `claude_flow/web/templates/index.html` (JS section)

### Step 1: Update fetchTasks to refresh detail view

修改现有的 `fetchTasks` 函数（line 1164-1172），在 `renderTaskList()` 之后加入详情页刷新逻辑：

```javascript
async function fetchTasks() {
    var data = await api('/tasks');
    if (data !== null) {
        allTasks = data;
        if (currentView === 'board') {
            renderTaskList();
        } else if (currentView === 'detail' && detailTaskId) {
            var task = allTasks.find(function(t) { return t.id === detailTaskId; });
            if (task) renderDetailPage(task);
        }
        updateNavCounts();
        fetchStatus();
    }
}
```

### Step 2: Clean up polling on view switch

在 `showBoardView` 函数中添加清理：

```javascript
function showBoardView() {
    currentView = 'board';
    detailTaskId = null;
    stopDetailChatPolling();
    stopDetailLogRefresh();
    // ... (rest of existing code)
}
```

---

## Task 8: Cleanup & Edge Cases

### Step 1: Handle Escape key for detail page

修改 keyboard shortcuts handler（line 1936-1944）：

```javascript
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        if (currentView === 'detail') {
            // If any modal is open, close it first; otherwise go back to board
            var anyModalOpen = document.querySelector('.modal-overlay.active');
            if (anyModalOpen) {
                closeNewTaskModal(); closeRespondModal(); closePlanModal(); closeLogModal(); closeChatModal();
            } else {
                navigateToBoard();
            }
        } else {
            closeNewTaskModal(); closeChatModal(); closeRespondModal(); closePlanModal(); closeLogModal();
        }
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
        e.preventDefault();
        openNewTaskModal();
    }
});
```

### Step 2: Ensure modals still work from board view

现有的模态框保持不变，作为从看板快速操作的入口。模态框关闭后如果有状态变化会通过 auto-refresh 同步到详情页。

---

## Summary of Changes

| Category | Additions | Notes |
|----------|-----------|-------|
| CSS | ~200 lines | Detail page layout, sidebar, tabs, responsive |
| HTML | ~30 lines | Detail page container |
| JS Router | ~60 lines | Hash routing, view switching |
| JS Detail Render | ~100 lines | Info panel, tabs, overview |
| JS Plan Tab | ~30 lines | Inline plan rendering |
| JS Chat Tab | ~120 lines | Inline chat with polling |
| JS Log Tab | ~50 lines | Inline log with auto-refresh |
| JS Actions | ~80 lines | Detail page action handlers |
| JS Modifications | ~20 lines | fetchTasks, keyboard, init |
| **Total** | **~690 lines** | Pure frontend, 0 backend changes |

## Key Design Decisions

1. **Hash routing** - SPA 体验，浏览器前进/后退可用，无需后端新增路由
2. **Sidebar 隐藏** - 详情页全宽显示，避免多层导航嵌套
3. **Tab 内嵌** - Chat/Plan/Log 直接嵌入页面，无模态框割裂感
4. **保留模态框** - 看板上的快速操作入口不变，兼容现有习惯
5. **独立 polling** - 详情页 chat/log 有独立的轮询计时器，切换视图时正确清理
6. **Auto-refresh 兼容** - 3s 全局刷新同时更新详情页数据
