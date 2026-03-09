# Task Detail Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a task detail page with hash-based routing, replacing modal-based interaction with Tab-embedded views for an immersive task management experience.

**Architecture:** Hash-based routing in the existing SPA single HTML file (`/#/` = board, `/#/task/<id>` = detail page). Detail page uses left info panel + right tab-switched content. Chat/Plan/Log migrate from modals to inline tabs while preserving modal shortcuts on the board.

**Tech Stack:** Vanilla JS (hash routing), CSS Grid/Flexbox, existing API endpoints

**Backend:** No changes required. All existing API endpoints cover the detail page needs.

---

## Current Code Structure (index.html, 1959 lines)

| Section | Lines | Content |
|---------|-------|---------|
| CSS | 7-741 | All styles (sidebar, cards, modals, guide, etc.) |
| HTML Body | 743-1099 | Sidebar, main content, guide page, 5 modals, toast |
| JS | 1104-1957 | Global state, API, data fetch, tab nav, rendering, actions, modals, utils, init |

### Key JS Functions & Their Line Numbers

| Function | Line | Purpose |
|----------|------|---------|
| `fetchTasks` | 1164 | Fetch all tasks, call renderTaskList |
| `switchTab` | 1190 | Board tab navigation |
| `renderTaskList` | 1234 | Render filtered task cards |
| `renderCard` | 1248 | Build single card HTML |
| `buildActions` | 1311 | Card action buttons |
| `openChatModal` | 1429 | Chat modal open + init |
| `sendChatMessage` | 1481 | Send chat message + start polling |
| `startChatPolling` | 1514 | Poll for AI response |
| `viewPlan` | 1705 | Open plan modal |
| `viewLog` | 1746 | Open log modal |
| `renderStructuredLog` | 1776 | Structured log renderer |
| `renderMarkdown` | 1653 | Markdown to HTML |
| Keyboard shortcuts | 1936 | Escape, Ctrl+N |
| Init | 1953 | switchTab, fetchTasks, startAutoRefresh |

---

## Page Layout

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

## Route Table

| Hash | View | Description |
|------|------|-------------|
| `#/` or empty | Board View | Current board (task list + sidebar nav) |
| `#/task/<task_id>` | Detail View | Task detail page (new) |

## Tab Definitions

| Tab | Content | Notes |
|-----|---------|-------|
| Overview | Prompt full text, metadata, timeline | Default tab |
| Plan | Plan document (Markdown rendered) | Shows Approve button when `planned` |
| Chat | Interactive chat UI | Reuses existing chat logic, independent polling |
| Log | Execution log | Auto-refresh when `running` |

---

## Task 1: CSS Styles for Detail Page

**Files:** Modify `claude_flow/web/templates/index.html` (CSS section, insert before line 741 `</style>`)

### What to add (~200 lines CSS)

Insert all detail page CSS before the closing `</style>` tag (line 741). The CSS covers:

1. **`.detail-page`** - Main container, `display:none` by default, `display:flex` when `.active`
2. **`.detail-top-bar`** - Top bar with back button, task ID, title, status badge
3. **`.detail-back-btn`** - Transparent button with hover state transitioning to `#00d4ff`
4. **`.detail-task-id`** - Monospace font, small, muted color
5. **`.detail-task-title`** - 18px bold, `flex:1` to fill space
6. **`.detail-status-badge`** - Uppercase, small, rounded, colored by status
7. **`.detail-body`** - Flex row container for sidebar + main
8. **`.detail-sidebar`** - 260px fixed width, dark background, scrollable, with sections for info/prompt/actions
9. **`.detail-info-section h3`** - Section headers (INFORMATION, PROMPT PREVIEW, ACTIONS)
10. **`.detail-info-row`** - Label/value pairs in the sidebar
11. **`.detail-prompt-preview`** - Dark box with monospace text, max-height 120px
12. **`.detail-actions`** - Column of full-width buttons
13. **`.detail-main`** - Flex column, `flex:1`, contains tabs + content
14. **`.detail-tabs`** - Tab bar with bottom border, `#12122a` background
15. **`.detail-tab`** - Tab button with bottom border indicator, active state `#00d4ff`
16. **`.detail-tab-content`** - Scrollable content area, padding 20px
17. **Overview tab styles** - `.detail-overview-prompt`, `.detail-timeline`, `.timeline-item`, `.timeline-dot`
18. **Chat tab styles** - `.detail-chat-container`, `.detail-chat-messages`, `.detail-chat-input-area`, `.detail-chat-typing`
19. **Plan/Log tab styles** - `.detail-plan-actions`, `.detail-log-actions`
20. **Mobile responsive** - `@media (max-width: 768px)` rules: sidebar becomes top bar (100% width, max-height 200px), actions become horizontal row, tabs scroll horizontally

**Design consistency:** All colors, borders, backgrounds, fonts match existing theme (`#12122a`, `#2a2a4a`, `#0f0f23`, `#00d4ff`, etc.).

---

## Task 2: HTML Container for Detail Page

**Files:** Modify `claude_flow/web/templates/index.html` (HTML body, insert after line 1008 closing `</div>` of guide page)

### What to add (~28 lines HTML)

Insert a `<div class="detail-page" id="detailPage">` container with this structure:

```
detailPage
  ├── detail-top-bar
  │   ├── detail-back-btn (onclick="navigateToBoard()")
  │   ├── #detailTaskId (span)
  │   ├── #detailTaskTitle (span)
  │   └── #detailStatusBadge (span)
  └── detail-body
      ├── detail-sidebar
      │   ├── detail-info-section "Information"
      │   │   └── #detailInfoRows (dynamic)
      │   ├── detail-info-section "Prompt Preview"
      │   │   └── #detailPromptPreview (div)
      │   └── detail-info-section "Actions"
      │       └── #detailActions (dynamic)
      └── detail-main
          ├── #detailTabs (dynamic tab buttons)
          └── #detailTabContent (dynamic content)
```

All content is dynamically rendered via JS - the HTML is just the skeleton.

---

## Task 3: Hash Router + View Switching

**Files:** Modify `claude_flow/web/templates/index.html` (JS section)

### Step 1: Add global state variables (insert near line 1114, after existing globals)

```
let currentView = 'board';    // 'board' | 'detail'
let detailTaskId = null;       // Current detail page task ID
let detailActiveTab = 'overview'; // Active tab in detail page
```

### Step 2: Add router functions (insert before Init section, ~line 1953)

**`initRouter()`** - Attach `hashchange` listener, call `handleRoute()`

**`handleRoute()`** - Parse `window.location.hash`:
- Match `#/task/<id>` → `showDetailView(id)`
- Otherwise → `showBoardView()`

**`navigateToBoard()`** - Set `window.location.hash = '#/'`

**`navigateToTask(taskId)`** - Set `window.location.hash = '#/task/' + taskId`

**`showBoardView()`** - Reset state, show sidebar/topBar/contentPanel, hide detailPage, stop detail polling, restore guide page state if needed, call `renderTaskList()`

**`showDetailView(taskId)`** - Set state, hide sidebar/topBar/contentPanel/guidePage, show detailPage, find task in `allTasks` or fetch via API, call `renderDetailPage(task)`

**`fetchTaskAndRenderDetail(taskId)`** - Fetch single task via `GET /api/tasks/<id>`, render if found, navigate to board if not found

### Step 3: Update Init section (line 1953-1956)

Add `initRouter()` call after existing init:
```javascript
switchTab(activeTab);
fetchTasks();
startAutoRefresh();
initRouter();
```

### Step 4: Add "Open" button to card headers

Modify `renderCard` function (line 1297-1308). In the card-header, add an "Open" button inside `.card-meta`:

```javascript
var openBtn = '<button class="btn btn-outline btn-small" onclick="event.stopPropagation(); navigateToTask(\'' + task.id + '\')" style="margin-left:4px;">Open</button>';
```

Insert `openBtn` before `metaHtml` in the card-meta div.

---

## Task 4: Detail Page Rendering (Info Panel + Overview Tab)

**Files:** Modify `claude_flow/web/templates/index.html` (JS section, after router code)

### Functions to implement (~100 lines)

**`renderDetailPage(task)`** - Master render function:
1. Set top bar: task ID, title, status badge (color from `STATUS_COLORS`)
2. Call `renderDetailInfo(task)` for sidebar info rows
3. Set prompt preview (first 200 chars)
4. Call `renderDetailActions(task)` for sidebar action buttons
5. Call `renderDetailTabs(task)` for tab bar
6. Call `renderDetailTabContent(task, detailActiveTab)` for current tab

**`renderDetailInfo(task)`** - Build info rows:
- Status, Priority (if >0), Branch (if set), Worker (if set), Plan Mode (if set)
- Created, Started (if set), Completed (if set), Error (if set, in red)
- Use `infoRow(label, value)` helper

**`infoRow(label, value)`** - Return HTML `<div class="detail-info-row">...</div>`

**`renderDetailActions(task)`** - Dynamic action buttons based on `task.status`:
- `pending`: Auto Plan, Chat Plan
- `planned`: Approve, View Plan, Chat
- `planning` + interactive: Open Chat
- `approved`: Run
- `running`/`done`/`failed`: View Log
- `needs_input`: Respond
- `failed`/`needs_input`/`running`: Reset
- `failed`: Retry
- Not `running`: Delete

**`renderDetailTabs(task)`** - Render 4 tab buttons: Overview, Plan, Chat, Log

**`detailSwitchTab(tabId)`** - Update active tab state, re-render tab content

**`renderDetailTabContent(task, tabId)`** - Switch/case to route to tab renderers

**`renderOverviewTab(task, container)`** - Full prompt display + timeline (Created/Started/Completed with colored dots) + error section if applicable

---

## Task 5: Plan Tab (Inline Rendering)

**Files:** Modify `claude_flow/web/templates/index.html` (JS section)

### Function (~30 lines)

**`renderPlanTab(task, container)`** (async):
1. If `planned` status: show action bar with "Approve Plan" + "Discuss in Chat" buttons
2. Set container to "Loading..."
3. If task has plan_file or status is `planned`/`approved`/`running`/`done`/`failed`:
   - Fetch `GET /api/tasks/<id>/plan`
   - Render with `renderMarkdown(data.content)` into `#detailPlanContent`
4. Otherwise: show "No plan yet" message

**Key:** Reuses existing `renderMarkdown()` and `.plan-content` CSS. Set `max-height:none` to remove the 500px limit from modal usage.

---

## Task 6: Chat Tab (Inline with Independent Polling)

**Files:** Modify `claude_flow/web/templates/index.html` (JS section)

### Functions (~120 lines)

**`detailChatPollingTimer`** - Module-level variable for detail chat polling

**`renderChatTab(task, container)`** (async):
1. Stop any existing detail chat polling
2. Build chat UI: action bar (with "Generate Plan" button), message area, typing indicator, input + send button
3. Load existing messages via `GET /api/tasks/<id>/chat`
4. If `thinking=true`: disable input, show typing indicator, start polling
5. Focus input

**`renderDetailChatMessages(messages)`** - Render messages into `#detailChatMessages`, reuse `.chat-bubble` CSS classes

**`sendDetailChatMessage()`**:
1. Read input, add user bubble immediately
2. Disable input, show typing indicator
3. POST to `/api/tasks/<id>/chat`
4. If accepted, start polling

**`startDetailChatPolling(taskId)`** - Poll every 1.5s:
- Fetch chat data
- When `thinking=false`: stop polling, enable input, append AI bubble, scroll down, focus input

**`stopDetailChatPolling()`** - Clear interval timer

**`detailFinalizeChat()`** - POST to `/api/tasks/<id>/chat/finalize`, show toast, refresh tasks

**Important:** This is a completely independent polling system from the modal chat. The modal chat uses `chatPollingTimer` (line 1478), the detail page uses `detailChatPollingTimer`. Both can coexist but won't run simultaneously (different views).

---

## Task 7: Log Tab (Inline with Auto-Refresh)

**Files:** Modify `claude_flow/web/templates/index.html` (JS section)

### Functions (~50 lines)

**`detailLogAutoRefresh`** - Module-level variable for log auto-refresh timer

**`renderLogTab(task, container)`** (async):
1. Stop previous auto-refresh
2. Build action bar: Refresh button + auto-refresh indicator (if running)
3. Set content to "Loading..."
4. Call `loadDetailLog(taskId)`
5. If `running`: start 3s interval auto-refresh

**`loadDetailLog(taskId)`** (async):
- Fetch `GET /api/tasks/<id>/log`
- If structured: use existing `renderStructuredLog(data.data)`
- If raw: set textContent
- If no data: show "No log data available."

**`refreshDetailLog()`** - Manual refresh button handler

**`stopDetailLogRefresh()`** - Clear interval timer

**Key:** Reuses existing `renderStructuredLog()` and `.log-content` CSS. Set `max-height:none` for full-page viewing.

---

## Task 8: Auto-Refresh Integration + View Cleanup

**Files:** Modify `claude_flow/web/templates/index.html` (JS section)

### Step 1: Update `fetchTasks` (line 1164-1172)

Add detail view refresh logic after `renderTaskList()`:

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

### Step 2: Cleanup polling on view switch

In `showBoardView()`, add:
```javascript
stopDetailChatPolling();
stopDetailLogRefresh();
```

### Step 3: Update keyboard shortcuts (line 1936-1944)

```javascript
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        if (currentView === 'detail') {
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

### Step 4: Add click-outside-to-close for detail page modals

No changes needed - existing modal click-outside handlers still work since modals are global overlays.

---

## Summary of Changes

| Category | Additions | Notes |
|----------|-----------|-------|
| CSS | ~200 lines | Detail page layout, sidebar, tabs, responsive |
| HTML | ~28 lines | Detail page container skeleton |
| JS Router | ~60 lines | Hash routing, view switching |
| JS Detail Render | ~100 lines | Info panel, tabs, overview |
| JS Plan Tab | ~30 lines | Inline plan rendering |
| JS Chat Tab | ~120 lines | Inline chat with independent polling |
| JS Log Tab | ~50 lines | Inline log with auto-refresh |
| JS Modifications | ~30 lines | fetchTasks, keyboard, init, card Open button |
| **Total** | **~618 lines** | Pure frontend, 0 backend changes |

## Execution Order & Dependencies

```
Task 1 (CSS) ──────────────┐
Task 2 (HTML) ─────────────┤── No dependencies between 1 & 2
                            ├── Task 3 depends on 1 + 2 (needs containers to show/hide)
Task 3 (Router + Open btn) ┤
                            ├── Task 4 depends on 3 (renderDetailPage called by router)
Task 4 (Render + Overview) ┤
                            ├── Tasks 5, 6, 7 depend on 4 (tab switching calls them)
Task 5 (Plan Tab) ─────────┤── 5, 6, 7 are independent of each other
Task 6 (Chat Tab) ─────────┤
Task 7 (Log Tab) ──────────┤
                            ├── Task 8 depends on 3, 6, 7 (modifies fetchTasks, adds cleanup)
Task 8 (Integration) ──────┘
```

**Parallelizable:** Task 1 + 2 can be done in parallel. Tasks 5 + 6 + 7 can be done in parallel.

## Key Design Decisions

1. **Hash routing** - SPA experience, browser back/forward works, no backend route changes
2. **Sidebar hidden** - Detail page uses full width, avoids nested navigation layers
3. **Tab inline** - Chat/Plan/Log embedded in page, no modal context-switching
4. **Modals preserved** - Board quick-action modals unchanged, backward compatible
5. **Independent polling** - Detail chat/log have separate timers from modal chat, properly cleaned up on view switch
6. **Auto-refresh compatible** - 3s global refresh updates both board and detail views
7. **Responsive** - Mobile detail page: sidebar collapses to top, actions become horizontal row, tabs scroll horizontally

## Verification Checklist

- [ ] Hash routing works: `/#/` shows board, `/#/task/<id>` shows detail
- [ ] Browser back/forward navigates between board and detail
- [ ] "Open" button on cards navigates to detail page
- [ ] Detail page shows correct task info, status badge, actions
- [ ] All 4 tabs render correctly: Overview, Plan, Chat, Log
- [ ] Chat sends messages, polls for response, displays AI reply
- [ ] Log shows structured/raw content, auto-refreshes for running tasks
- [ ] Plan renders markdown, shows Approve button for planned tasks
- [ ] Escape key: closes modals first, then navigates to board
- [ ] Auto-refresh (3s) updates detail page without losing tab state
- [ ] View switching properly cleans up chat/log polling timers
- [ ] Mobile layout: sidebar collapses, tabs scroll, actions wrap
- [ ] Existing board functionality (modals, card expand, etc.) unaffected
