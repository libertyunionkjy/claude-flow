# Chat Status Feedback Enhancement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve interactive plan chat UX by surfacing AI reply notifications on task cards and providing clear finalize status feedback in the chat modal.

**Architecture:** Backend enriches `/tasks` list response with chat metadata (`chat_thinking`, `chat_has_reply`) for `planning+interactive` tasks, eliminating N+1 requests. Frontend uses this data to show sub-state indicators on cards and implements proper finalize loading/polling flow.

**Tech Stack:** Python (Flask API), Vanilla JS, CSS animations

**Backend:** One change to `list_tasks` in `api.py` to inject chat metadata fields.

---

## Current Code Structure (key locations)

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| `list_tasks` | `claude_flow/web/api.py` | 34-49 | GET /api/tasks - returns `[t.to_dict()]` |
| `finalize_chat` | `claude_flow/web/api.py` | 248-295 | POST /chat/finalize - async plan gen |
| `ChatSession` | `claude_flow/chat.py` | 42-69 | `thinking`, `status`, `messages` fields |
| `ChatManager.get_session` | `claude_flow/chat.py` | 147-169 | Dead thread detection |
| `renderCard` | `web/templates/index.html` | 1248-1310 | Card HTML generation |
| `buildActions` | `web/templates/index.html` | 1311-1360 | Card action buttons |
| Status indicator | `web/templates/index.html` | 1267-1273 | `planning-indicator` div |
| `.planning-indicator` CSS | `web/templates/index.html` | 339-347 | Indicator styles |
| `finalizeChat` | `web/templates/index.html` | 1544-1552 | Generate Plan handler |
| Chat modal HTML | `web/templates/index.html` | 1039-1058 | Modal structure |
| `startChatPolling` | `web/templates/index.html` | 1514-1542 | 1.5s polling loop |
| `fetchTasks` | `web/templates/index.html` | 1164-1189 | 3s auto-refresh |

---

## Part A: Chat Reply Awareness Enhancement

### Task 1: Backend - Enrich `/tasks` with chat metadata

**Files:**
- Modify: `claude_flow/web/api.py:34-49`
- Test: `tests/test_web_api.py`

**Step 1: Write the failing test**

Add to `tests/test_web_api.py`:

```python
def test_list_tasks_includes_chat_metadata(client, task_manager, chat_manager):
    """Planning+interactive tasks include chat_thinking and chat_has_reply."""
    # Create a task in planning state with interactive mode
    task = task_manager.add("Test", "prompt")
    task_manager.update_status(task.id, TaskStatus.PLANNING)
    task_manager.update_plan_mode(task.id, "interactive")

    # Create chat session with an assistant reply
    session = chat_manager.create_session(task.id, mode="interactive")
    session.messages.append(ChatMessage(role="user", content="hello"))
    session.messages.append(ChatMessage(role="assistant", content="hi"))
    chat_manager._save_session(session)

    resp = client.get("/api/tasks")
    data = resp.get_json()["data"]
    t = next(d for d in data if d["id"] == task.id)

    assert t["chat_thinking"] is False
    assert t["chat_has_reply"] is True


def test_list_tasks_no_chat_metadata_for_non_interactive(client, task_manager):
    """Non-interactive tasks should not have chat metadata."""
    task = task_manager.add("Test", "prompt")

    resp = client.get("/api/tasks")
    data = resp.get_json()["data"]
    t = next(d for d in data if d["id"] == task.id)

    assert "chat_thinking" not in t
    assert "chat_has_reply" not in t
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_api.py::test_list_tasks_includes_chat_metadata -v`
Expected: FAIL - `chat_thinking` key missing from response

**Step 3: Write minimal implementation**

Modify `claude_flow/web/api.py` `list_tasks` function (lines 34-49):

```python
@api_bp.route("/tasks", methods=["GET"])
def list_tasks():
    """获取所有任务列表，支持 ?status= 筛选。"""
    tm = current_app.config["TASK_MANAGER"]
    chat_mgr = current_app.config["CHAT_MANAGER"]
    status_filter = request.args.get("status")

    if status_filter:
        try:
            task_status = TaskStatus(status_filter)
        except ValueError:
            return _err(f"无效的状态值: {status_filter}")
        tasks = tm.list_tasks(status=task_status)
    else:
        tasks = tm.list_tasks()

    result = []
    for t in tasks:
        d = t.to_dict()
        if t.status == TaskStatus.PLANNING and t.plan_mode == "interactive":
            session = chat_mgr.get_session(t.id)
            if session:
                d["chat_thinking"] = session.thinking
                d["chat_has_reply"] = (
                    len(session.messages) > 0
                    and session.messages[-1].role == "assistant"
                )
            else:
                d["chat_thinking"] = False
                d["chat_has_reply"] = False
        result.append(d)

    return _ok(result)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_web_api.py::test_list_tasks_includes_chat_metadata tests/test_web_api.py::test_list_tasks_no_chat_metadata_for_non_interactive -v`
Expected: PASS

**Step 5: Run full test suite to check for regressions**

Run: `pytest tests/test_web_api.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add claude_flow/web/api.py tests/test_web_api.py
git commit -m "feat(api): enrich /tasks response with chat metadata for interactive planning tasks"
```

---

### Task 2: Frontend - Card planning-indicator sub-state

**Files:**
- Modify: `claude_flow/web/templates/index.html:1267-1273` (status indicator in `renderCard`)
- Modify: `claude_flow/web/templates/index.html:339-347` (CSS)

**Step 1: Add CSS for reply indicator pulse animation**

Add after `.planning-indicator` styles (line 347):

```css
.chat-reply-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #00b894;
    display: inline-block;
    animation: pulse-dot 1.5s ease-in-out infinite;
}

@keyframes pulse-dot {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(1.4); }
}
```

**Step 2: Update status indicator logic in `renderCard`**

Replace lines 1267-1273 in `renderCard`:

```javascript
// Status indicators
var statusIndicator = '';
if (status === 'planning') {
    if (task.plan_mode === 'interactive') {
        if (task.chat_thinking) {
            statusIndicator = '<div class="planning-indicator"><span class="spinner"></span>AI is thinking...</div>';
        } else if (task.chat_has_reply) {
            statusIndicator = '<div class="planning-indicator"><span class="chat-reply-dot"></span>AI has replied</div>';
        } else {
            statusIndicator = '<div class="planning-indicator">Waiting for input...</div>';
        }
    } else {
        statusIndicator = '<div class="planning-indicator"><span class="spinner"></span>Generating plan...</div>';
    }
} else if (status === 'running') {
    statusIndicator = '<div class="running-indicator"><span class="spinner"></span>Executing...</div>';
}
```

**Step 3: Verify visually**

1. Start the dev server: `cf web`
2. Create a task and start interactive planning
3. Verify: card shows "Waiting for input..." initially
4. Send a message, verify: card shows "AI is thinking..." with spinner
5. After AI responds, verify: card shows "AI has replied" with green pulsing dot

**Step 4: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): add sub-state indicators for interactive planning cards"
```

---

### Task 3: Frontend - Open Chat button badge

**Files:**
- Modify: `claude_flow/web/templates/index.html:1325-1328` (`buildActions` planning case)
- Modify: `claude_flow/web/templates/index.html` (CSS section)

**Step 1: Add badge CSS**

Add in CSS section (after the `.chat-reply-dot` styles from Task 2):

```css
.btn-badge {
    position: relative;
}

.btn-badge::after {
    content: 'New Reply';
    position: absolute;
    top: -6px;
    right: -8px;
    background: #00b894;
    color: #fff;
    font-size: 9px;
    padding: 1px 5px;
    border-radius: 6px;
    font-weight: 600;
    white-space: nowrap;
}
```

**Step 2: Update `buildActions` for planning case**

Replace lines 1325-1328:

```javascript
case 'planning':
    if (task.plan_mode === 'interactive') {
        var badgeClass = task.chat_has_reply ? ' btn-badge' : '';
        btns.push(
            '<button class="btn btn-warning btn-small' + badgeClass
            + '" onclick="openChatModal(event, \'' + id + '\')">'
            + 'Open Chat</button>'
        );
    }
    break;
```

**Step 3: Verify visually**

1. With an interactive planning task that has an AI reply
2. Verify: "Open Chat" button shows a "New Reply" badge
3. With a task where AI is still thinking: no badge

**Step 4: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): add 'New Reply' badge to Open Chat button when AI has responded"
```

---

## Part B: Finalize Status Feedback

### Task 4: Frontend - Disable button + loading state on finalize

**Files:**
- Modify: `claude_flow/web/templates/index.html:1544-1552` (`finalizeChat` function)
- Modify: `claude_flow/web/templates/index.html:1039-1058` (chat modal HTML)

**Step 1: Add `finalizingTaskId` global state**

Add after line 1108 (`let chatTaskId = null;`):

```javascript
let finalizingTaskId = null;
```

**Step 2: Rewrite `finalizeChat` function**

Replace lines 1544-1552:

```javascript
async function finalizeChat() {
    if (!chatTaskId || finalizingTaskId) return;

    // Disable all interactive elements
    finalizingTaskId = chatTaskId;
    var finalizeBtn = document.getElementById('chatFinalizeBtn');
    var sendBtn = document.getElementById('chatSendBtn');
    var chatInput = document.getElementById('chatInput');

    finalizeBtn.disabled = true;
    finalizeBtn.innerHTML = '<span class="spinner"></span>Generating plan...';
    sendBtn.disabled = true;
    chatInput.disabled = true;

    var data = await api('/tasks/' + chatTaskId + '/chat/finalize', { method: 'POST' });
    if (data !== null) {
        showToast('Plan generation started...');
        // Do NOT close modal - start polling for completion
        startFinalizePolling(chatTaskId);
    } else {
        // Restore button state on error
        finalizingTaskId = null;
        finalizeBtn.disabled = false;
        finalizeBtn.textContent = 'Generate Plan';
        sendBtn.disabled = false;
        chatInput.disabled = false;
    }
}
```

**Step 3: Verify the button disables on click**

1. Open chat with some messages
2. Click "Generate Plan"
3. Verify: button changes to spinner + "Generating plan..." and is disabled
4. Verify: Send button and input are also disabled
5. Verify: Modal stays open (no longer closes immediately)

**Step 4: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): disable finalize button with loading state during plan generation"
```

---

### Task 5: Frontend - Poll for finalize completion

**Files:**
- Modify: `claude_flow/web/templates/index.html` (add `startFinalizePolling` function, near `finalizeChat`)

**Step 1: Add finalize polling function**

Add after the rewritten `finalizeChat` function:

```javascript
var finalizePollingTimer = null;

function startFinalizePolling(taskId) {
    stopFinalizePolling();
    finalizePollingTimer = setInterval(async function() {
        var data = await api('/tasks/' + taskId);
        if (data === null) { stopFinalizePolling(); return; }

        if (data.status === 'planned') {
            // Success - plan generated
            stopFinalizePolling();
            finalizingTaskId = null;
            var finalizeBtn = document.getElementById('chatFinalizeBtn');
            finalizeBtn.innerHTML = 'Plan generated!';
            finalizeBtn.classList.remove('btn-success');
            finalizeBtn.classList.add('btn-outline');

            // Add a success message to chat
            var container = document.getElementById('chatMessages');
            var sysBubble = document.createElement('div');
            sysBubble.className = 'chat-bubble assistant';
            sysBubble.innerHTML = '<div class="chat-role">System</div>'
                + '<p style="color:#00b894;">Plan has been generated successfully! '
                + 'You can close this dialog and review it.</p>';
            container.appendChild(sysBubble);
            container.scrollTop = container.scrollHeight;

            showToast('Plan generated for ' + taskId);
            fetchTasks();

        } else if (data.status === 'failed') {
            // Failure
            stopFinalizePolling();
            finalizingTaskId = null;
            var finalizeBtn = document.getElementById('chatFinalizeBtn');
            finalizeBtn.disabled = false;
            finalizeBtn.textContent = 'Generate Plan';
            document.getElementById('chatSendBtn').disabled = false;
            document.getElementById('chatInput').disabled = false;

            var container = document.getElementById('chatMessages');
            var errBubble = document.createElement('div');
            errBubble.className = 'chat-bubble assistant';
            errBubble.innerHTML = '<div class="chat-role">System</div>'
                + '<p style="color:#ff6b6b;">Plan generation failed: '
                + escapeHtml(data.error || 'Unknown error')
                + '. You can try again.</p>';
            container.appendChild(errBubble);
            container.scrollTop = container.scrollHeight;

            showToast('Plan generation failed', 'error');
            fetchTasks();
        }
        // else: still planning, continue polling
    }, 2000);
}

function stopFinalizePolling() {
    if (finalizePollingTimer) {
        clearInterval(finalizePollingTimer);
        finalizePollingTimer = null;
    }
}
```

**Step 2: Update `closeChatModal` to clean up finalize state**

Find the existing `closeChatModal` function and add cleanup at the top:

```javascript
function closeChatModal() {
    stopChatPolling();
    stopFinalizePolling();
    finalizingTaskId = null;
    // Reset finalize button state
    var finalizeBtn = document.getElementById('chatFinalizeBtn');
    finalizeBtn.disabled = false;
    finalizeBtn.textContent = 'Generate Plan';
    finalizeBtn.classList.remove('btn-outline');
    finalizeBtn.classList.add('btn-success');
    // ... existing code (chatTaskId = null, hide modal, etc.) ...
}
```

**Step 3: Verify the full finalize flow**

1. Open chat, have a conversation, click "Generate Plan"
2. Verify: button shows spinner + "Generating plan..."
3. Verify: all inputs disabled
4. Verify: when plan completes, success message appears in chat
5. Verify: button text changes to "Plan generated!"
6. Close modal, verify: task card now shows "planned" status

**Step 4: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): poll for finalize completion and show result in chat modal"
```

---

### Task 6: Frontend - Prevent duplicate finalize trigger

**Files:**
- Modify: `claude_flow/web/templates/index.html` (guard in `sendChatMessage`)

**Step 1: Add guard to `sendChatMessage`**

At the top of `sendChatMessage` function (line 1481), add:

```javascript
async function sendChatMessage() {
    if (!chatTaskId) return;
    if (finalizingTaskId) return;  // <-- add this guard
    // ... rest of function unchanged ...
}
```

**Step 2: Also guard the Enter key handler**

The textarea `onkeydown` handler (line 1053) already calls `sendChatMessage()`, so the guard in Step 1 covers this.

**Step 3: Verify**

1. Start finalize process
2. Try typing in input and pressing Enter: nothing happens (input is disabled + guard)
3. Try clicking Send: nothing happens (button is disabled + guard)
4. Try clicking "Generate Plan" again: nothing happens (button is disabled + `finalizingTaskId` check)

**Step 4: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat(ui): guard against duplicate finalize and message sends during plan generation"
```

---

## Verification Checklist

After all tasks are complete, verify end-to-end:

| # | Scenario | Expected Result |
|---|----------|----------------|
| 1 | Interactive planning task, AI is thinking | Card shows spinner + "AI is thinking..." |
| 2 | AI has responded, user hasn't opened chat | Card shows green pulsing dot + "AI has replied" |
| 3 | AI has responded, Open Chat button | "New Reply" badge visible |
| 4 | User opens chat after AI reply | Messages visible, no need to guess |
| 5 | User clicks "Generate Plan" | Button disables, shows spinner + "Generating plan..." |
| 6 | Modal stays open during finalize | User sees loading state in the modal |
| 7 | Plan generation completes | Success message in chat, button changes text |
| 8 | Plan generation fails | Error message in chat, button re-enables for retry |
| 9 | Double-click "Generate Plan" | Only one request sent |
| 10 | Type/send during finalize | Inputs disabled, no action |
| 11 | Auto plan (non-interactive) task | Card still shows "Generating plan..." as before |
| 12 | Close modal during finalize | Polling stops, state resets cleanly |

---

## Summary of Changes

| File | Type | Description |
|------|------|-------------|
| `claude_flow/web/api.py` | Backend | Inject `chat_thinking` + `chat_has_reply` into task list response |
| `tests/test_web_api.py` | Test | 2 new tests for chat metadata in list response |
| `claude_flow/web/templates/index.html` | Frontend CSS | Pulse animation `.chat-reply-dot`, badge `.btn-badge` |
| `claude_flow/web/templates/index.html` | Frontend JS | Sub-state in `renderCard`, badge in `buildActions`, finalize loading + polling |
