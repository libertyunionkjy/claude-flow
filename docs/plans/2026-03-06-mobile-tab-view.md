# Mobile Tab View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在移动端（<=768px）将看板的 8 列切换为 Tab 导航模式，每次只显示一列，通过点击 Tab 切换。

**Architecture:** 在 `index.html` 中新增一个 Tab 导航栏组件（仅移动端可见），通过 CSS media query 控制显隐。移动端隐藏所有非 active 列，通过 JS 管理 active 状态切换。桌面端行为完全不变。

**Tech Stack:** 原生 HTML5 + CSS3 + JavaScript（无框架，与现有代码风格一致）

---

## 现状分析

### 当前文件

- **唯一需要修改的文件**: `claude_flow/web/templates/index.html`（单页应用，CSS/JS 全部内联）

### 当前看板结构

```
.header          -- 顶部导航（标题 + 操作按钮）
.board           -- 看板容器（flex 横向排列，overflow-x: auto）
  .column[8个]   -- 8 个状态列（min-width:260px, max-width:300px）
    .column-header  -- 列标题（状态名 + 计数）
    .column-body    -- 卡片容器
```

### 当前 8 个状态

```javascript
const STATUSES = ['pending', 'planning', 'planned', 'approved', 'running', 'needs_input', 'done', 'failed'];
```

### 当前响应式代码（CSS 第 486-511 行）

移动端仅缩小了列宽和间距，仍然需要横向滚动才能查看所有列。

---

## Task 1: 添加 Tab 导航栏 HTML 结构

**Files:**
- Modify: `claude_flow/web/templates/index.html:527-528`（在 `<!-- 看板主体 -->` 注释和 `<div class="board">` 之间插入）

**Step 1: 在 board 元素之前插入 Tab 导航栏 HTML**

在 `index.html` 第 527 行（`<!-- 看板主体 -->` 注释）和第 529 行（`<div class="board" id="board">`）之间插入：

```html
    <!-- 移动端 Tab 导航（仅小屏显示） -->
    <div class="mobile-tabs" id="mobileTabs">
        <div class="mobile-tabs-scroll">
            <button class="tab-btn active" data-status="pending" onclick="switchTab('pending')">
                Pending <span class="tab-count" id="tab-count-pending">0</span>
            </button>
            <button class="tab-btn" data-status="planning" onclick="switchTab('planning')">
                Planning <span class="tab-count" id="tab-count-planning">0</span>
            </button>
            <button class="tab-btn" data-status="planned" onclick="switchTab('planned')">
                Planned <span class="tab-count" id="tab-count-planned">0</span>
            </button>
            <button class="tab-btn" data-status="approved" onclick="switchTab('approved')">
                Approved <span class="tab-count" id="tab-count-approved">0</span>
            </button>
            <button class="tab-btn" data-status="running" onclick="switchTab('running')">
                Running <span class="tab-count" id="tab-count-running">0</span>
            </button>
            <button class="tab-btn" data-status="needs_input" onclick="switchTab('needs_input')">
                Input <span class="tab-count" id="tab-count-needs_input">0</span>
            </button>
            <button class="tab-btn" data-status="done" onclick="switchTab('done')">
                Done <span class="tab-count" id="tab-count-done">0</span>
            </button>
            <button class="tab-btn" data-status="failed" onclick="switchTab('failed')">
                Failed <span class="tab-count" id="tab-count-failed">0</span>
            </button>
        </div>
    </div>
```

**Step 2: 验证 HTML 结构正确**

在浏览器打开页面（桌面端），确认 Tab 栏存在于 DOM 中但尚无样式。

**Step 3: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(web): add mobile tab navigation HTML structure"
```

---

## Task 2: 添加 Tab 导航栏 CSS 样式

**Files:**
- Modify: `claude_flow/web/templates/index.html:486-511`（替换现有 `@media (max-width: 768px)` 块）

**Step 1: 在现有 `@media` 块之前添加 Tab 栏基础样式**

在 CSS 中（第 485 行，`.running-indicator` 样式块结束后），添加 Tab 栏默认样式（桌面端隐藏）：

```css
/* -- 移动端 Tab 导航 ------------------------------------------------ */
.mobile-tabs {
    display: none;  /* 桌面端隐藏 */
}
```

**Step 2: 替换 `@media (max-width: 768px)` 块为完整的移动端适配样式**

将现有的 `@media (max-width: 768px)` 整个块（第 486-511 行）替换为：

```css
/* -- 响应式设计 ------------------------------------------------------ */
@media (max-width: 768px) {
    .header {
        padding: 10px 14px;
        flex-wrap: wrap;
        gap: 8px;
    }

    .header h1 {
        font-size: 16px;
    }

    /* Tab 导航栏 */
    .mobile-tabs {
        display: block;
        position: sticky;
        top: 0;
        z-index: 50;
        background: #0f0f23;
        border-bottom: 1px solid #2a2a4a;
        padding: 0;
    }

    .mobile-tabs-scroll {
        display: flex;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;  /* Firefox */
        padding: 8px 12px;
        gap: 6px;
    }

    .mobile-tabs-scroll::-webkit-scrollbar {
        display: none;  /* Chrome/Safari */
    }

    .tab-btn {
        flex-shrink: 0;
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 500;
        color: #8a8aaa;
        background: transparent;
        border: 1px solid #2a2a4a;
        border-radius: 16px;
        cursor: pointer;
        white-space: nowrap;
        transition: all 0.2s ease;
        font-family: inherit;
    }

    .tab-btn.active {
        color: #fff;
        border-color: #00d4ff;
        background: rgba(0, 212, 255, 0.15);
    }

    .tab-btn .tab-count {
        display: inline-block;
        min-width: 16px;
        height: 16px;
        line-height: 16px;
        text-align: center;
        font-size: 10px;
        background: rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        margin-left: 4px;
        padding: 0 4px;
    }

    .tab-btn.active .tab-count {
        background: rgba(0, 212, 255, 0.3);
    }

    /* 隐藏非 active 状态的 tab-count 为 0 的标记 */
    .tab-btn .tab-count.zero {
        opacity: 0.4;
    }

    /* 看板：移动端单列全宽显示 */
    .board {
        padding: 12px;
        gap: 0;
        flex-direction: column;
        overflow-x: hidden;
    }

    .column {
        min-width: 100%;
        max-width: 100%;
        display: none;  /* 默认隐藏所有列 */
        max-height: none;  /* 移除桌面端的高度限制 */
    }

    .column.mobile-active {
        display: flex;  /* 仅显示 active 列 */
    }

    /* 移动端隐藏列头（信息已在 Tab 栏中展示） */
    .column.mobile-active .column-header {
        display: none;
    }

    .modal {
        width: 95%;
        padding: 18px;
    }
}
```

**Step 3: 在桌面端验证 Tab 栏不可见**

在浏览器（宽度 > 768px）中确认 `.mobile-tabs` 为 `display: none`。

**Step 4: 在移动端（或 DevTools 模拟）验证 Tab 栏可见**

将浏览器窗口缩小到 <= 768px，确认：
- Tab 栏可见且可横向滚动
- 所有列默认隐藏
- 首个 Tab（Pending）高亮

**Step 5: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(web): add mobile tab navigation CSS with responsive design"
```

---

## Task 3: 添加 Tab 切换 JavaScript 逻辑

**Files:**
- Modify: `claude_flow/web/templates/index.html`（JS 部分，在 `renderBoard()` 函数附近）

**Step 1: 添加全局状态变量**

在 JS 全局变量区域（约第 668 行，`let refreshTimer = null;` 之后）添加：

```javascript
let activeTab = 'pending';   // 当前激活的移动端 Tab
```

**Step 2: 添加 `switchTab()` 函数**

在 `renderBoard()` 函数之后（约第 753 行）添加：

```javascript
function switchTab(status) {
    activeTab = status;

    // 更新 Tab 按钮 active 状态
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.status === status);
    });

    // 更新列的显示状态
    document.querySelectorAll('.column').forEach(col => {
        col.classList.toggle('mobile-active', col.dataset.status === status);
    });
}
```

**Step 3: 在 `renderBoard()` 末尾同步 Tab 计数和 active 状态**

在 `renderBoard()` 函数内部，`fetchStatus();` 调用之前（约第 752 行）插入：

```javascript
    // 同步移动端 Tab 计数
    STATUSES.forEach(status => {
        const tabCount = document.getElementById('tab-count-' + status);
        if (tabCount) {
            const count = groups[status].length;
            tabCount.textContent = count;
            tabCount.classList.toggle('zero', count === 0);
        }
    });

    // 恢复移动端 active 列
    switchTab(activeTab);
```

**Step 4: 在移动端验证功能**

将浏览器缩小到 <= 768px：
1. 确认首次加载默认显示 Pending 列
2. 点击各个 Tab 按钮，确认列正确切换
3. 确认 Tab 按钮上的计数与列中任务数一致
4. 确认 Tab 栏可以横向滑动查看所有 Tab

**Step 5: 在桌面端验证无影响**

将浏览器恢复到 > 768px，确认看板行为完全不变。

**Step 6: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(web): implement mobile tab switching logic"
```

---

## Task 4: 智能 Tab 自动切换（可选增强）

**Files:**
- Modify: `claude_flow/web/templates/index.html`（JS 部分）

**Step 1: 增强 `renderBoard()` 的智能切换逻辑**

在 `renderBoard()` 中同步 Tab 计数的代码之后，添加"当前 Tab 为空时自动跳到有任务的 Tab"逻辑：

```javascript
    // 如果当前 active Tab 为空，自动切换到第一个有任务的 Tab
    if (groups[activeTab].length === 0) {
        const firstNonEmpty = STATUSES.find(s => groups[s].length > 0);
        if (firstNonEmpty) {
            activeTab = firstNonEmpty;
        }
    }
```

此代码应放在 `switchTab(activeTab)` 调用之前。

**Step 2: 验证智能切换**

1. 创建一个任务（Pending 状态）
2. 在移动端查看 Tab 切换到 Pending
3. Plan 该任务（变为 Planning/Planned）
4. 确认 Tab 自动从 Pending 跳到有任务的 Tab

**Step 3: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(web): auto-switch to non-empty tab when current tab is empty"
```

---

## Task 5: Tab 栏状态颜色同步

**Files:**
- Modify: `claude_flow/web/templates/index.html`（CSS 部分）

**Step 1: 为每个状态的 Tab 按钮添加 active 颜色**

在 `@media (max-width: 768px)` 块中 `.tab-btn.active` 样式之后添加状态专属颜色：

```css
    /* Tab 按钮状态颜色 */
    .tab-btn[data-status="pending"].active    { border-color: #6c5ce7; background: rgba(108, 92, 231, 0.15); }
    .tab-btn[data-status="planning"].active   { border-color: #a29bfe; background: rgba(162, 155, 254, 0.15); }
    .tab-btn[data-status="planned"].active    { border-color: #74b9ff; background: rgba(116, 185, 255, 0.15); }
    .tab-btn[data-status="approved"].active   { border-color: #00cec9; background: rgba(0, 206, 201, 0.15); }
    .tab-btn[data-status="running"].active    { border-color: #fdcb6e; background: rgba(253, 203, 110, 0.15); }
    .tab-btn[data-status="needs_input"].active { border-color: #e17055; background: rgba(225, 112, 85, 0.15); }
    .tab-btn[data-status="done"].active       { border-color: #00b894; background: rgba(0, 184, 148, 0.15); }
    .tab-btn[data-status="failed"].active     { border-color: #ff6b6b; background: rgba(255, 107, 107, 0.15); }
```

**Step 2: 验证颜色一致性**

在移动端点击每个 Tab，确认 active 状态的边框色和背景色与桌面端列标题颜色一致。

**Step 3: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(web): sync tab button colors with column header colors"
```

---

## 总结

### 变更范围

| 项目 | 说明 |
|------|------|
| 修改文件数 | **1 个** (`index.html`) |
| 新增 HTML | ~30 行（Tab 导航栏） |
| 新增 CSS | ~90 行（Tab 样式 + 移动端列显隐） |
| 新增 JS | ~25 行（`switchTab()` + 计数同步 + 智能切换） |
| 修改 CSS | 替换原有 `@media` 块（扩展而非删除） |
| 桌面端影响 | **零影响**（Tab 栏 `display: none`，无 JS 副作用） |

### 设计决策

| 决策 | 理由 |
|------|------|
| Tab 栏使用 `position: sticky` | 滚动时 Tab 栏始终可见，便于随时切换 |
| Tab 栏使用胶囊按钮（pill button）样式 | 比传统下划线 Tab 更适合深色主题和触摸操作 |
| 移动端隐藏 column-header | 信息已在 Tab 中展示，避免重复 |
| 计数为 0 时半透明显示 | 快速区分有任务/无任务的 Tab |
| 智能自动切换作为可选增强 | 改善体验但非核心功能，可独立测试 |
| 颜色与桌面端列标题同步 | 保持视觉一致性，用户无需重新建立颜色-状态的认知映射 |

### 原则应用

- **KISS**: 纯 CSS + 少量 JS 实现，无新依赖，无框架引入
- **DRY**: Tab 计数复用 `renderBoard()` 的 groups 数据，颜色与列标题复用同一色系
- **YAGNI**: 不引入 swipe 手势、动画过渡等非必要功能
- **SRP**: `switchTab()` 职责单一 —— 仅切换 active 状态
- **OCP**: 新增 `.mobile-active` class 控制显隐，未修改现有 `.column` 的默认行为
