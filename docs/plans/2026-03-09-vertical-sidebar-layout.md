# Vertical Sidebar Layout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将前端从横向 8 列看板重构为纵向布局 — 桌面端使用左侧垂直 Tab 栏 + 右侧内容区，移动端使用汉堡菜单侧滑出 Tab 列表。

**Architecture:** 重构单一文件 `index.html` 的 CSS 布局（从 flex 横向改为左右分栏）、HTML 结构（统一 Tab 栏组件，增加汉堡按钮和遮罩层）和 JS 交互逻辑（统一 Tab 切换函数，增加汉堡菜单开关逻辑）。桌面端 Tab 栏常驻左侧，移动端收缩为汉堡菜单。

**Tech Stack:** 原生 HTML5 + CSS3 + JavaScript（与现有代码风格一致，无框架）

---

## 现状分析

### 当前文件

- **唯一需要修改的文件**: `claude_flow/web/templates/index.html`（~2462 行，CSS/JS 全部内联）

### 当前布局结构

```
.header              -- 顶部固定导航栏（sticky）
.mobile-tabs         -- 移动端水平 Tab 栏（仅 <=768px，position: sticky）
.board               -- 看板容器（flex 横向，overflow-x: auto）
  .column[8个]       -- 8 个状态列（min-width:260px, max-width:300px）
.guide-page          -- 工作流指南（与 board 互斥）
5 个 modal-overlay   -- 模态框
```

### 目标布局结构

```
.header              -- 顶部导航栏（增加汉堡按钮，仅移动端可见）
.app-body            -- 新增容器（flex 横向）
  .sidebar           -- 左侧垂直 Tab 栏（桌面端常驻，移动端侧滑）
  .main-content      -- 右侧内容区
    .board           -- 只显示当前 active Tab 对应的列
    .guide-page      -- 工作流指南
.sidebar-overlay     -- 移动端侧边栏遮罩层
5 个 modal-overlay   -- 模态框（不变）
```

### 关键代码位置索引

| 组件 | CSS 行号 | HTML 行号 | JS 行号 |
|------|----------|-----------|---------|
| `.header` | 22-46 | 1136-1147 | - |
| `.board` / `.column` | 132-187 | 1179-1229 | `renderBoard()` 1616-1645 |
| `.mobile-tabs` / `.mobile-tab` | 980-1048 | 1149-1177 | `switchMobileTab()` 1648-1682 |
| `@media <=768px` | 1051-1131 | - | - |
| `.guide-page` | 749-978 | 1232-1461 | `toggleGuidePage()` 2383-2405 |
| 全局变量 | - | - | 1558-1566 |

---

## Task 1: 重构 HTML 结构 — 添加 sidebar + app-body 容器

**Files:**
- Modify: `claude_flow/web/templates/index.html:1134-1461`（HTML body 部分）

**Step 1: 重构 HTML 结构**

将 `<body>` 内的 HTML 结构从：

```html
<div class="header">...</div>
<nav class="mobile-tabs" id="mobileTabs">...</nav>
<div class="board" id="board">...</div>
<div class="guide-page" id="guidePage">...</div>
```

重构为：

```html
<body>
    <!-- 顶部导航 -->
    <div class="header">
        <div class="header-left">
            <button class="hamburger-btn" id="hamburgerBtn" onclick="toggleSidebar()">
                <span></span><span></span><span></span>
            </button>
            <h1>CLAUDE FLOW</h1>
        </div>
        <div class="header-actions">
            <!-- 保持原有按钮不变 -->
            <div class="status-bar" id="statusBar">loading...</div>
            <button class="btn btn-outline btn-small" onclick="planAllTasks()" title="Plan All Pending">Plan All</button>
            <button class="btn btn-outline btn-small" onclick="approveAllTasks()" title="Approve All Planned">Approve All</button>
            <button class="btn btn-outline btn-small" onclick="runAllTasks()" title="Run All Approved">Run All</button>
            <button class="btn btn-outline btn-small" onclick="retryAllTasks()" title="Retry All Failed">Retry All</button>
            <button class="btn btn-outline btn-small" id="guideToggleBtn" onclick="toggleGuidePage()">Guide</button>
            <button class="btn btn-primary" onclick="openNewTaskModal()">+ New Task</button>
        </div>
    </div>

    <!-- 主体: sidebar + content -->
    <div class="app-body">
        <!-- 左侧 Tab 栏 -->
        <aside class="sidebar" id="sidebar">
            <nav class="sidebar-tabs">
                <button class="sidebar-tab active" data-tab="pending" onclick="switchTab('pending')">
                    <span class="sidebar-tab-dot" style="background:#6c5ce7;"></span>
                    <span class="sidebar-tab-label">Pending</span>
                    <span class="sidebar-tab-count" id="tab-count-pending">0</span>
                </button>
                <button class="sidebar-tab" data-tab="planning" onclick="switchTab('planning')">
                    <span class="sidebar-tab-dot" style="background:#a29bfe;"></span>
                    <span class="sidebar-tab-label">Planning</span>
                    <span class="sidebar-tab-count" id="tab-count-planning">0</span>
                </button>
                <button class="sidebar-tab" data-tab="planned" onclick="switchTab('planned')">
                    <span class="sidebar-tab-dot" style="background:#74b9ff;"></span>
                    <span class="sidebar-tab-label">Planned</span>
                    <span class="sidebar-tab-count" id="tab-count-planned">0</span>
                </button>
                <button class="sidebar-tab" data-tab="approved" onclick="switchTab('approved')">
                    <span class="sidebar-tab-dot" style="background:#00cec9;"></span>
                    <span class="sidebar-tab-label">Approved</span>
                    <span class="sidebar-tab-count" id="tab-count-approved">0</span>
                </button>
                <button class="sidebar-tab" data-tab="running" onclick="switchTab('running')">
                    <span class="sidebar-tab-dot" style="background:#fdcb6e;"></span>
                    <span class="sidebar-tab-label">Running</span>
                    <span class="sidebar-tab-count" id="tab-count-running">0</span>
                </button>
                <button class="sidebar-tab" data-tab="needs_input" onclick="switchTab('needs_input')">
                    <span class="sidebar-tab-dot" style="background:#e17055;"></span>
                    <span class="sidebar-tab-label">Needs Input</span>
                    <span class="sidebar-tab-count" id="tab-count-needs_input">0</span>
                </button>
                <button class="sidebar-tab" data-tab="done" onclick="switchTab('done')">
                    <span class="sidebar-tab-dot" style="background:#00b894;"></span>
                    <span class="sidebar-tab-label">Done</span>
                    <span class="sidebar-tab-count" id="tab-count-done">0</span>
                </button>
                <button class="sidebar-tab" data-tab="failed" onclick="switchTab('failed')">
                    <span class="sidebar-tab-dot" style="background:#ff6b6b;"></span>
                    <span class="sidebar-tab-label">Failed</span>
                    <span class="sidebar-tab-count" id="tab-count-failed">0</span>
                </button>
            </nav>
        </aside>

        <!-- 右侧内容区 -->
        <main class="main-content" id="mainContent">
            <!-- 看板主体 (保持原有 8 列结构不变) -->
            <div class="board" id="board">
                <!-- 8 个 .column 保持原样 -->
            </div>

            <!-- 工作流指南页面 (保持原有结构不变) -->
            <div class="guide-page" id="guidePage">
                <!-- 内容不变 -->
            </div>
        </main>
    </div>

    <!-- 移动端侧边栏遮罩 -->
    <div class="sidebar-overlay" id="sidebarOverlay" onclick="closeSidebar()"></div>

    <!-- 原有 5 个模态框保持不变 -->
    <!-- ... -->
```

注意：
- 删除原有的 `<nav class="mobile-tabs">` 整个块（行 1149-1177）
- 在 `header` 内部用 `.header-left` 包裹新增的汉堡按钮和 `h1`
- 8 个 `.column` div 保持原样不变
- `.guide-page` 移入 `.main-content` 内部
- 在所有 `modal-overlay` 之前添加 `.sidebar-overlay`

**Step 2: 确认 HTML 结构正确，页面不报错**

在浏览器打开页面，确认 DOM 结构正确，无 JS 报错（此时样式会错乱，属正常现象）。

---

## Task 2: 重写 CSS — 桌面端左侧 sidebar + 右侧内容区布局

**Files:**
- Modify: `claude_flow/web/templates/index.html`（CSS 部分）

**Step 1: 删除旧的移动端 Tab 样式**

删除以下 CSS 区域：
- `.mobile-tabs` 相关样式（行 980-998）
- `.mobile-tab` 相关样式（行 1000-1048）
- 整个 `@media (max-width: 768px)` 块（行 1051-1131）

**Step 2: 修改 `.header` 样式**

将现有 `.header` 样式（行 22-46）修改，增加 `.header-left` 和 `.hamburger-btn` 样式：

```css
/* -- 顶部导航栏 ------------------------------------------------------ */
.header {
    background: #0f0f23;
    border-bottom: 1px solid #2a2a4a;
    padding: 12px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
}

.header-left {
    display: flex;
    align-items: center;
    gap: 12px;
}

.header h1 {
    font-size: 18px;
    font-weight: 600;
    color: #00d4ff;
    letter-spacing: 1px;
}

/* 汉堡按钮 (桌面端隐藏) */
.hamburger-btn {
    display: none;
    flex-direction: column;
    justify-content: center;
    gap: 4px;
    width: 32px;
    height: 32px;
    padding: 6px;
    background: transparent;
    border: 1px solid #2a2a4a;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.2s ease;
}

.hamburger-btn span {
    display: block;
    width: 100%;
    height: 2px;
    background: #8888aa;
    border-radius: 1px;
    transition: all 0.2s ease;
}

.hamburger-btn:hover {
    border-color: #00d4ff;
}

.hamburger-btn:hover span {
    background: #00d4ff;
}
```

**Step 3: 添加 app-body 和 sidebar 样式**

在 `.header` 相关样式之后、按钮样式之前（行 48 附近），添加新的布局样式：

```css
/* -- 主体布局 -------------------------------------------------------- */
.app-body {
    display: flex;
    min-height: calc(100vh - 57px);  /* 减去 header 高度 */
}

/* -- 左侧 Tab 栏 ----------------------------------------------------- */
.sidebar {
    width: 180px;
    flex-shrink: 0;
    background: #0f0f23;
    border-right: 1px solid #2a2a4a;
    display: flex;
    flex-direction: column;
    position: sticky;
    top: 57px;  /* header 高度 */
    height: calc(100vh - 57px);
    overflow-y: auto;
    z-index: 50;
}

.sidebar-tabs {
    display: flex;
    flex-direction: column;
    padding: 8px;
    gap: 2px;
}

.sidebar-tab {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    background: transparent;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s ease;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    color: #6c7a9a;
    text-align: left;
    width: 100%;
}

.sidebar-tab:hover {
    background: rgba(255, 255, 255, 0.05);
    color: #a0a0c0;
}

.sidebar-tab.active {
    background: rgba(0, 212, 255, 0.08);
    color: #e0e0e0;
}

.sidebar-tab-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}

.sidebar-tab-label {
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.sidebar-tab-count {
    font-size: 11px;
    min-width: 20px;
    height: 20px;
    line-height: 20px;
    text-align: center;
    background: rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    padding: 0 6px;
    flex-shrink: 0;
}

.sidebar-tab.active .sidebar-tab-count {
    background: rgba(0, 212, 255, 0.2);
    color: #00d4ff;
}

.sidebar-tab-count.zero {
    opacity: 0.3;
}

/* -- 右侧内容区 ------------------------------------------------------- */
.main-content {
    flex: 1;
    min-width: 0;  /* 防止 flex 子元素溢出 */
    overflow-y: auto;
}

/* -- 侧边栏遮罩 (桌面端隐藏) ------------------------------------------ */
.sidebar-overlay {
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0, 0, 0, 0.5);
    z-index: 90;
}

.sidebar-overlay.active {
    display: block;
}
```

**Step 4: 修改 `.board` 样式为纵向单列**

将现有 `.board` 样式（行 132-140）修改为：

```css
/* -- 看板主体 -------------------------------------------------------- */
.board {
    display: flex;
    flex-direction: column;
    gap: 0;
    padding: 20px;
    min-height: calc(100vh - 57px);
}

.column {
    width: 100%;
    max-width: 100%;
    min-width: unset;
    flex: none;
    background: transparent;
    border-radius: 0;
    display: none;  /* 默认隐藏所有列 */
    flex-direction: column;
    max-height: none;
}

.column.active {
    display: flex;
}

.column-header {
    padding: 14px 16px;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
    margin-bottom: 12px;
}
```

注意：`.column-header` 的颜色样式（行 174-181）保持不变。

**Step 5: 修改 `.column-body` 样式为网格布局**

```css
.column-body {
    padding: 0;
    overflow-y: visible;
    flex: 1;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
}
```

**Step 6: 修改 `.card` 样式适配新布局**

```css
.card {
    background: #16213e;
    border-radius: 8px;
    padding: 12px;
    cursor: pointer;
    transition: all 0.2s ease;
    border: 1px solid transparent;
    margin-bottom: 0;  /* 改用 grid gap */
}
```

---

## Task 3: 添加移动端响应式样式 — 汉堡菜单 + 侧滑 sidebar

**Files:**
- Modify: `claude_flow/web/templates/index.html`（CSS 部分）

**Step 1: 添加新的 `@media` 响应式块**

在 CSS 末尾（`</style>` 之前）添加：

```css
/* -- 响应式设计: 移动端 ---------------------------------------------- */
@media (max-width: 768px) {
    .header {
        padding: 10px 14px;
        flex-wrap: wrap;
        gap: 8px;
    }

    .header h1 {
        font-size: 16px;
    }

    /* 显示汉堡按钮 */
    .hamburger-btn {
        display: flex;
    }

    /* sidebar 改为侧滑抽屉 */
    .sidebar {
        position: fixed;
        top: 0;
        left: -260px;
        width: 260px;
        height: 100vh;
        z-index: 200;
        transition: left 0.3s ease;
        padding-top: 16px;
        border-right: 1px solid #2a2a4a;
        box-shadow: none;
    }

    .sidebar.open {
        left: 0;
        box-shadow: 4px 0 20px rgba(0, 0, 0, 0.5);
    }

    /* sidebar 内添加标题区域（移动端侧边栏顶部） */
    .sidebar-tabs {
        padding: 12px;
        gap: 2px;
    }

    .sidebar-tab {
        padding: 12px 14px;
        font-size: 14px;
    }

    /* 主内容区 */
    .app-body {
        flex-direction: column;
    }

    .board {
        padding: 12px;
    }

    .column-body {
        grid-template-columns: 1fr;
    }

    .modal {
        width: 95%;
        padding: 18px;
    }

    /* 工作流指南页面适配 */
    .guide-page {
        padding: 16px;
    }

    .guide-stepper {
        padding-left: 28px;
    }

    .guide-stepper::before {
        left: 11px;
    }

    .guide-step-dot {
        left: -23px;
        width: 10px;
        height: 10px;
        top: 16px;
    }

    .guide-cmd-row {
        grid-template-columns: 1fr;
    }

    .guide-step-body {
        padding: 0 12px 12px 36px;
    }

    .guide-step-header {
        padding: 12px;
        gap: 8px;
    }

    .guide-header h2 {
        font-size: 20px;
    }
}
```

---

## Task 4: 重写 JavaScript — 统一 Tab 切换 + 汉堡菜单逻辑

**Files:**
- Modify: `claude_flow/web/templates/index.html`（JS 部分）

**Step 1: 修改全局状态变量**

将 JS 全局变量区域（行 1558-1566）修改为：

```javascript
/* -- 全局状态 -------------------------------------------------------- */
let allTasks = [];
let expandedCards = new Set();
let chatTaskId = null;
let respondingTaskId = null;
let viewingPlanTaskId = null;
let viewingLogTaskId = null;
let refreshTimer = null;
let activeTab = 'pending';      // 当前激活的 Tab（统一桌面/移动端）
let sidebarOpen = false;        // 移动端侧边栏状态
```

删除原有的 `let activeMobileTab = 'pending';`。

**Step 2: 替换 `switchMobileTab()` 和 `syncMobileTabState()` 为统一的 `switchTab()`**

删除原有的 `switchMobileTab()` 函数（行 1648-1666）和 `syncMobileTabState()` 函数（行 1668-1682），替换为：

```javascript
/* -- Tab 切换 -------------------------------------------------------- */
function switchTab(status) {
    activeTab = status;

    // 更新 sidebar Tab 按钮 active 状态
    document.querySelectorAll('.sidebar-tab').forEach(function(tab) {
        tab.classList.toggle('active', tab.dataset.tab === status);
    });

    // 更新列显示状态
    document.querySelectorAll('.column').forEach(function(col) {
        col.classList.toggle('active', col.dataset.status === status);
    });

    // 移动端自动关闭侧边栏
    if (window.innerWidth <= 768) {
        closeSidebar();
    }
}

function syncTabCounts() {
    STATUSES.forEach(function(status) {
        var tabCount = document.getElementById('tab-count-' + status);
        var colCount = document.getElementById('count-' + status);
        if (tabCount && colCount) {
            var count = colCount.textContent;
            tabCount.textContent = count;
            tabCount.classList.toggle('zero', count === '0');
        }
    });
}
```

**Step 3: 添加汉堡菜单 JS 函数**

在 `switchTab()` 之后添加：

```javascript
/* -- 汉堡菜单 -------------------------------------------------------- */
function toggleSidebar() {
    if (sidebarOpen) {
        closeSidebar();
    } else {
        openSidebar();
    }
}

function openSidebar() {
    sidebarOpen = true;
    document.getElementById('sidebar').classList.add('open');
    document.getElementById('sidebarOverlay').classList.add('active');
}

function closeSidebar() {
    sidebarOpen = false;
    document.getElementById('sidebar').classList.remove('open');
    document.getElementById('sidebarOverlay').classList.remove('active');
}
```

**Step 4: 修改 `renderBoard()` 函数**

在 `renderBoard()` 函数内部末尾，将原有的 `syncMobileTabState()` 调用替换为：

```javascript
    fetchStatus();
    syncTabCounts();

    // 如果当前 active Tab 为空，自动切换到第一个有任务的 Tab
    if (groups[activeTab].length === 0) {
        var firstNonEmpty = STATUSES.find(function(s) { return groups[s].length > 0; });
        if (firstNonEmpty && firstNonEmpty !== activeTab) {
            activeTab = firstNonEmpty;
        }
    }

    // 恢复 active 列
    switchTab(activeTab);
```

**Step 5: 修改 `toggleGuidePage()` 函数适配新布局**

将原有的 `toggleGuidePage()` 函数（行 2383-2405）修改为：

```javascript
function toggleGuidePage() {
    guideVisible = !guideVisible;
    var guidePage = document.getElementById('guidePage');
    var board = document.getElementById('board');
    var sidebar = document.getElementById('sidebar');
    var btn = document.getElementById('guideToggleBtn');

    if (guideVisible) {
        guidePage.classList.add('active');
        board.style.display = 'none';
        sidebar.style.display = 'none';
        btn.textContent = 'Board';
        btn.classList.remove('btn-outline');
        btn.classList.add('btn-info');
    } else {
        guidePage.classList.remove('active');
        board.style.display = '';
        sidebar.style.display = '';
        btn.textContent = 'Guide';
        btn.classList.remove('btn-info');
        btn.classList.add('btn-outline');
    }
}
```

**Step 6: 修改初始化代码**

将底部初始化代码（行 2455-2458）修改为：

```javascript
/* -- 初始化 ---------------------------------------------------------- */
switchTab(activeTab);
fetchTasks();
startAutoRefresh();
```

**Step 7: 修改 Escape 键盘事件**

在键盘事件处理中（行 2430-2443），添加 sidebar 关闭支持：

```javascript
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        closeSidebar();
        closeNewTaskModal();
        closeChatModal();
        closeRespondModal();
        closePlanModal();
        closeLogModal();
    }
    // Ctrl+N: New task
    if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
        e.preventDefault();
        openNewTaskModal();
    }
});
```

---

## Task 5: 验证和调优

**Step 1: 桌面端验证**

1. 打开浏览器（宽度 > 768px）
2. 确认左侧 sidebar 常驻显示，包含 8 个 Tab
3. 确认右侧内容区显示当前 active Tab 对应的任务卡片
4. 点击每个 Tab，确认任务列表正确切换
5. 确认 Tab 计数与实际任务数一致
6. 确认汉堡按钮不可见
7. 确认 Guide 页面切换正常

**Step 2: 移动端验证**

1. 将浏览器缩小到 <= 768px（或使用 DevTools 移动端模拟）
2. 确认 sidebar 默认隐藏
3. 确认汉堡按钮可见
4. 点击汉堡按钮，确认 sidebar 从左侧滑出
5. 确认遮罩层显示
6. 点击 Tab，确认任务列表切换且 sidebar 自动关闭
7. 点击遮罩层，确认 sidebar 关闭
8. 确认模态框正常打开/关闭

**Step 3: 修复可能的细节问题**

- 检查 `.guide-page` 在新布局中是否正确占满右侧内容区
- 检查 `.column-header` 是否与 sidebar Tab 视觉一致
- 检查 Toast 消息位置是否正常
- 检查自动刷新是否正常工作

---

## 总结

### 变更范围

| 项目 | 说明 |
|------|------|
| 修改文件数 | **1 个** (`claude_flow/web/templates/index.html`) |
| CSS 变更 | 删除 ~70 行旧移动端样式，新增 ~150 行 sidebar + 响应式样式 |
| HTML 变更 | 删除 `mobile-tabs` 块，新增 `app-body` + `sidebar` + `sidebar-overlay` 结构 |
| JS 变更 | 删除 `switchMobileTab()` + `syncMobileTabState()`，新增 `switchTab()` + `syncTabCounts()` + 汉堡菜单函数 |
| 无变更 | 所有 API 调用、模态框逻辑、Card 渲染逻辑、Markdown 渲染器 |

### 设计决策

| 决策 | 理由 |
|------|------|
| sidebar 宽度 180px | 足够容纳 "Needs Input" 等较长文字，不会过度压缩内容区 |
| 使用 CSS Grid 展示卡片 | `auto-fill + minmax(280px, 1fr)` 在宽屏可多列，窄屏自动单列 |
| 移动端 sidebar 宽度 260px | 稍宽于桌面端，适配触摸操作 |
| 移动端使用 `position: fixed` + transform | 侧滑抽屉模式，不影响主体内容布局 |
| 只显示当前 active 列 | 简化布局，符合纵向设计理念 |
| 保留 `.column-header` | 在内容区顶部展示当前状态名和计数，提供上下文 |

### 原则应用

- **KISS**: 纯 CSS 实现 sidebar 布局和侧滑动画，无 JS 动画库
- **DRY**: 统一 `switchTab()` 函数替代分离的桌面/移动端切换逻辑
- **YAGNI**: 不引入 swipe 手势、收缩/展开动画、Tab 拖拽排序等
- **SRP**: sidebar 切换、Tab 切换、汉堡菜单各自独立函数
- **OCP**: 新增 `.active` class 控制列显隐，`.column` 的 `data-status` 属性复用
