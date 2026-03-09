# Plan Feedback Workflow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Reject action with a unified Feedback action in the plan review workflow, so user replies are treated as neutral feedback rather than rejections.

**Architecture:** Remove `planner.reject()`, replace the `/reject` API endpoint with `/feedback`, and rewire the frontend. The existing `planner.generate_interactive(task, feedback=...)` already handles the correct prompt construction with neutral "user feedback" wording. CLI's reject option is merged into the existing feedback option.

**Tech Stack:** Python (Flask API), HTML/CSS/JS (frontend), pytest (tests)

---

### Task 1: Remove `reject()` from Planner and update tests

**Files:**
- Modify: `claude_flow/planner.py:108-110`
- Modify: `tests/test_planner.py:55-61`

**Step 1: Update the test**

Replace `test_reject_appends_reason` with a test that verifies `reject()` no longer exists:

```python
def test_reject_removed(self, tmp_path):
    """reject() method should no longer exist on Planner."""
    planner = self._make_planner(tmp_path)
    assert not hasattr(planner, 'reject')
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_planner.py::TestPlanner::test_reject_removed -v`
Expected: FAIL (reject still exists)

**Step 3: Remove the `reject()` method from `planner.py`**

Delete lines 108-110 in `claude_flow/planner.py`:

```python
# DELETE these lines:
#     def reject(self, task: Task, reason: str) -> None:
#         task.prompt += f"\n\n注意：上次的方案被拒绝，原因：{reason}，请重新规划。"
#         task.status = TaskStatus.PENDING
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_planner.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add claude_flow/planner.py tests/test_planner.py
git commit -m "refactor: remove planner.reject() in favor of feedback workflow"
```

---

### Task 2: Replace `/reject` API endpoint with `/feedback`

**Files:**
- Modify: `claude_flow/web/api.py:163-193`
- Modify: `tests/test_web_api.py:124-147`

**Step 1: Update the tests**

Replace `test_reject_task` and `test_reject_task_persists_prompt` with feedback tests:

```python
def test_feedback_task(self, client, tm, web_app):
    """Feedback triggers re-planning with user message."""
    task = tm.add("T1", "P1")
    tm.update_status(task.id, TaskStatus.PLANNED)

    # Mock generate_interactive to avoid calling Claude
    planner = web_app.config["PLANNER"]
    plans_dir = web_app.config["PROJECT_ROOT"] / ".claude-flow" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    def fake_generate_interactive(t, feedback=None):
        plan_file = plans_dir / f"{t.id}.md"
        plan_file.write_text(f"# Plan with feedback: {feedback}")
        t.status = TaskStatus.PLANNED
        t.plan_file = str(plan_file)
        return plan_file

    with patch.object(planner, 'generate_interactive', side_effect=fake_generate_interactive):
        resp = client.post(
            f"/api/tasks/{task.id}/feedback",
            json={"message": "Use option B"},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["status"] == "planning"

def test_feedback_task_wrong_status(self, client, tm, web_app):
    """Feedback only works on planned tasks."""
    task = tm.add("T1", "P1")
    resp = client.post(
        f"/api/tasks/{task.id}/feedback",
        json={"message": "some feedback"},
    )
    data = resp.get_json()
    assert data["ok"] is False

def test_feedback_task_empty_message(self, client, tm, web_app):
    """Feedback requires non-empty message."""
    task = tm.add("T1", "P1")
    tm.update_status(task.id, TaskStatus.PLANNED)
    resp = client.post(
        f"/api/tasks/{task.id}/feedback",
        json={"message": ""},
    )
    data = resp.get_json()
    assert data["ok"] is False
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_api.py::TestWebAPI::test_feedback_task tests/test_web_api.py::TestWebAPI::test_feedback_task_wrong_status tests/test_web_api.py::TestWebAPI::test_feedback_task_empty_message -v`
Expected: FAIL (endpoint doesn't exist yet)

**Step 3: Replace the reject endpoint with feedback endpoint**

Replace the reject endpoint in `claude_flow/web/api.py:163-193` with:

```python
@api_bp.route("/tasks/<task_id>/feedback", methods=["POST"])
def feedback_task(task_id: str):
    """Provide feedback on a plan to trigger re-generation.

    body: {message}. Only works on planned tasks.
    Status flow: planned -> planning -> planned (async).
    """
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"Task {task_id} not found", 404)

    if task.status != TaskStatus.PLANNED:
        return _err(
            f"Task {task_id} is {task.status.value}, feedback only works on planned tasks"
        )

    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return _err("message is required")

    # Transition: planned -> planning
    tm.update_status(task_id, TaskStatus.PLANNING)

    # Run re-generation in background thread
    def _regenerate():
        try:
            plan_file = planner.generate_interactive(task, feedback=message)
            if plan_file:
                tm.update_status(task_id, TaskStatus.PLANNED)
                _update_plan_file(tm, task_id, str(plan_file))
            else:
                tm.update_status(
                    task_id, TaskStatus.FAILED,
                    task.error or "Plan regeneration failed",
                )
        except Exception as e:
            tm.update_status(task_id, TaskStatus.FAILED, str(e))

    thread = threading.Thread(target=_regenerate, daemon=True)
    thread.start()

    updated = tm.get(task_id)
    return _ok(updated.to_dict())
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_web_api.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add claude_flow/web/api.py tests/test_web_api.py
git commit -m "feat: replace /reject endpoint with /feedback for neutral plan iteration"
```

---

### Task 3: Update integration test

**Files:**
- Modify: `tests/test_integration.py:122-141`

**Step 1: Update the integration test**

Replace `test_plan_reject_and_regenerate` to use `generate_interactive` with feedback instead of `reject` + `generate`:

```python
def test_plan_feedback_and_regenerate(self, cf_project, claude_subprocess_guard):
    """Plan feedback triggers regeneration via generate_interactive."""
    cfg, tm, planner, wt, worker = self._build_stack(cf_project)

    task = tm.add("Feat A", "Implement feature A")
    claude_subprocess_guard.set_plan_output("# Plan v1\nInitial plan")
    planner.generate(task)
    tm.update_status(task.id, TaskStatus.PLANNED)
    assert task.status == TaskStatus.PLANNED

    # Provide feedback and regenerate (no "rejected" wording)
    claude_subprocess_guard.set_plan_output("# Plan v2\nWith error handling")
    task.status = TaskStatus.PLANNING
    plan_file = planner.generate_interactive(task, feedback="needs error handling")
    assert plan_file is not None
    assert task.status == TaskStatus.PLANNED

    # Approve and verify
    planner.approve(task)
    tm.update_status(task.id, TaskStatus.APPROVED)
    assert task.status == TaskStatus.APPROVED
```

**Step 2: Run test**

Run: `pytest tests/test_integration.py::TestIntegrationWorkflow::test_plan_feedback_and_regenerate -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: update integration test to use feedback workflow"
```

---

### Task 4: Update CLI plan review

**Files:**
- Modify: `claude_flow/cli.py:384-389`

**Step 1: Remove the reject option from CLI plan review**

In `claude_flow/cli.py`, change the review prompt (line 377) from:

```python
"[a]pprove  [r]eject  [s]kip  [e]dit  [f]eedback  [q]uit"
```

to:

```python
"[a]pprove  [f]eedback  [s]kip  [e]dit  [q]uit"
```

Then remove the `elif action == "r":` block (lines 384-389):

```python
# DELETE these lines:
#             elif action == "r":
#                 _reset_terminal()
#                 reason = click.prompt("Rejection reason", default="")
#                 planner.reject(t, reason)
#                 tm.update_status(t.id, TaskStatus.PENDING)
#                 click.echo(f"  {t.id} rejected, back to pending")
```

**Step 2: Run CLI tests**

Run: `pytest tests/test_cli.py -v`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add claude_flow/cli.py
git commit -m "refactor: remove reject option from CLI plan review, feedback covers all cases"
```

---

### Task 5: Update frontend - replace Reject with Feedback

**Files:**
- Modify: `claude_flow/web/templates/index.html`

This task modifies multiple sections of the HTML file. All changes are within `index.html`.

**Step 1: Rename the modal (lines 831-843)**

Replace the reject modal with feedback modal:

```html
<!-- Feedback modal -->
<div class="modal-overlay" id="feedbackModal">
    <div class="modal">
        <h2>Feedback</h2>
        <div class="form-group">
            <label for="feedbackMessage">Your feedback</label>
            <textarea id="feedbackMessage" placeholder="Enter your feedback or reply..."></textarea>
        </div>
        <div class="modal-actions">
            <button class="btn btn-cancel" onclick="closeFeedbackModal()">Cancel</button>
            <button class="btn btn-warning" onclick="submitFeedback()">Submit</button>
        </div>
    </div>
</div>
```

**Step 2: Update global state variable (line 894)**

```javascript
// Change:
// let rejectingTaskId = null;
// To:
let feedbackTaskId = null;
```

**Step 3: Update card action buttons (line 1095)**

```javascript
// Change:
// btns.push(actionBtn('Reject', 'danger', 'openRejectModal', id));
// To:
btns.push(actionBtn('Feedback', 'warning', 'openFeedbackModal', id));
```

**Step 4: Replace reject JS functions (lines 1180-1204)**

```javascript
/* -- Task action: Feedback ----------------------------------------- */
function openFeedbackModal(event, taskId) {
    event.stopPropagation();
    feedbackTaskId = taskId;
    document.getElementById('feedbackMessage').value = '';
    document.getElementById('feedbackModal').classList.add('active');
}

function closeFeedbackModal() {
    feedbackTaskId = null;
    document.getElementById('feedbackModal').classList.remove('active');
}

async function submitFeedback() {
    if (!feedbackTaskId) return;
    const message = document.getElementById('feedbackMessage').value.trim();
    if (!message) {
        showToast('Please enter feedback');
        return;
    }
    const data = await api('/tasks/' + feedbackTaskId + '/feedback', {
        method: 'POST',
        body: JSON.stringify({ message: message }),
    });
    if (data !== null) {
        showToast('Feedback submitted, regenerating plan...');
        closeFeedbackModal();
        fetchTasks();
    }
}
```

**Step 5: Update Plan modal button (line 870)**

```html
<!-- Change: -->
<!-- <button class="btn btn-danger" id="planRejectBtn" onclick="rejectFromPlan()" style="display:none;">Reject</button> -->
<!-- To: -->
<button class="btn btn-warning" id="planFeedbackBtn" onclick="feedbackFromPlan()" style="display:none;">Feedback</button>
```

**Step 6: Update Plan modal JS (lines 1416-1452)**

Update the button visibility logic:

```javascript
// Change planRejectBtn to planFeedbackBtn
document.getElementById('planFeedbackBtn').style.display = isPlanned ? 'inline-block' : 'none';
```

Replace `rejectFromPlan()`:

```javascript
async function feedbackFromPlan() {
    if (!viewingPlanTaskId) return;
    feedbackTaskId = viewingPlanTaskId;
    closePlanModal();
    document.getElementById('feedbackMessage').value = '';
    document.getElementById('feedbackModal').classList.add('active');
}
```

**Step 7: Update keyboard shortcut handler (line 1568)**

```javascript
// Change closeRejectModal() to closeFeedbackModal()
closeFeedbackModal();
```

**Step 8: Update modal overlay click-to-close list (line 1581)**

```javascript
// Change 'rejectModal' to 'feedbackModal'
['newTaskModal', 'feedbackModal', 'respondModal', 'planModal', 'logModal'].forEach(...)
```

**Step 9: Verify in browser**

Open `cf web` and confirm:
- Planned task cards show "Feedback" button (yellow/warning style) instead of "Reject" (red)
- Clicking Feedback opens modal with "Your feedback" label
- Submitting feedback triggers re-planning (task goes to `planning` then back to `planned`)
- Plan modal shows "Feedback" button instead of "Reject"

**Step 10: Commit**

```bash
git add claude_flow/web/templates/index.html
git commit -m "feat: replace Reject button with Feedback in web frontend"
```

---

### Task 6: Full regression test

**Step 1: Run all tests**

Run: `pytest -v`
Expected: ALL PASS, no references to `planner.reject` remain.

**Step 2: Search for any remaining reject references**

Run: `rg "planner\.reject\|/reject\b" claude_flow/ tests/`
Expected: No matches.

**Step 3: Commit (if any cleanup needed)**

```bash
git add -A
git commit -m "chore: clean up remaining reject references"
```
