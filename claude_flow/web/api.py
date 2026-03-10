"""REST API 蓝图，提供任务管理和状态查询接口。"""
from __future__ import annotations

import json
import threading
from pathlib import Path

try:
    from flask import Blueprint, current_app, jsonify, request
except ImportError:
    raise ImportError(
        "Flask 未安装。请运行 `pip install flask` 以启用 Web Manager 功能。"
    )

from ..config import Config
from ..models import TaskStatus, TaskType
from ..worktree import WorktreeManager

api_bp = Blueprint("api", __name__, url_prefix="/api")

# Module-level registry for active workers by task_id.
# Used to stop worker subprocesses when deleting a running task.
_active_workers: dict = {}


def _ok(data):
    """成功响应。"""
    return jsonify({"ok": True, "data": data})


def _err(message: str, status_code: int = 400):
    """错误响应。"""
    return jsonify({"ok": False, "error": message}), status_code


# -- 任务列表 / 创建 ----------------------------------------------------------

def _enrich_task_dict(task_dict: dict) -> dict:
    """Enrich task dict with chat session state for frontend display."""
    chat_mgr = current_app.config.get("CHAT_MANAGER")
    if not chat_mgr:
        return task_dict
    task_id = task_dict.get("id")
    if not task_id:
        return task_dict
    session = chat_mgr.get_session(task_id)
    if session:
        task_dict["chat_thinking"] = session.thinking
        task_dict["chat_status"] = session.status
    else:
        task_dict["chat_thinking"] = False
        task_dict["chat_status"] = None
    return task_dict


@api_bp.route("/tasks", methods=["GET"])
def list_tasks():
    """获取所有任务列表，支持 ?status= 筛选。"""
    tm = current_app.config["TASK_MANAGER"]
    status_filter = request.args.get("status")

    if status_filter:
        try:
            task_status = TaskStatus(status_filter)
        except ValueError:
            return _err(f"无效的状态值: {status_filter}")
        tasks = tm.list_tasks(status=task_status)
    else:
        tasks = tm.list_tasks()

    return _ok([_enrich_task_dict(t.to_dict()) for t in tasks])


@api_bp.route("/tasks", methods=["POST"])
def create_task():
    """创建新任务。body: {title, prompt, priority}"""
    tm = current_app.config["TASK_MANAGER"]
    data = request.get_json(silent=True)

    if not data:
        return _err("请求体不能为空")

    title = data.get("title")
    prompt = data.get("prompt")

    if not title or not prompt:
        return _err("title 和 prompt 为必填字段")

    priority = 0
    raw_priority = data.get("priority")
    if raw_priority is not None:
        try:
            priority = int(raw_priority)
        except (ValueError, TypeError):
            return _err("priority 必须是整数")

    submodules = data.get("submodules", [])
    if not isinstance(submodules, list):
        return _err("submodules 必须是数组")

    task_type = data.get("task_type", "normal")
    if task_type == "mini":
        task = tm.add_mini(title, prompt, priority=priority, submodules=submodules)
    else:
        task = tm.add(title, prompt, priority=priority, submodules=submodules)
    return _ok(task.to_dict()), 201


# -- 单个任务操作 --------------------------------------------------------------

@api_bp.route("/tasks/<task_id>", methods=["GET"])
def get_task(task_id: str):
    """获取单个任务详情。"""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    return _ok(_enrich_task_dict(task.to_dict()))


@api_bp.route("/tasks/<task_id>", methods=["PATCH"])
def update_task(task_id: str):
    """更新任务（状态、优先级等）。"""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    data = request.get_json(silent=True)
    if not data:
        return _err("请求体不能为空")

    # 更新状态
    new_status = data.get("status")
    if new_status:
        try:
            status_enum = TaskStatus(new_status)
        except ValueError:
            return _err(f"无效的状态值: {new_status}")
        error_msg = data.get("error")
        task = tm.update_status(task_id, status_enum, error=error_msg)
        if not task:
            return _err(f"更新任务 {task_id} 状态失败")

    # 更新优先级
    raw_priority = data.get("priority")
    if raw_priority is not None:
        try:
            new_priority = int(raw_priority)
        except (ValueError, TypeError):
            return _err("priority 必须是整数")
        tm.update_priority(task_id, new_priority)

    # 重新获取更新后的任务
    updated = tm.get(task_id)
    return _ok(updated.to_dict())


@api_bp.route("/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id: str):
    """删除任务，自动停止关联的后台进程并清理资源。"""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    _cleanup_task_resources(task_id, task)

    if tm.remove(task_id) is not None:
        return _ok({"deleted": task_id})
    else:
        return _err(f"删除任务 {task_id} 失败")


def _cleanup_task_resources(task_id: str, task) -> None:
    """Clean up background processes and resources for a task before deletion.

    Handles:
    - planning: abort chat session (kills AI subprocess), clean up planner threads
    - running/merging: stop worker subprocess, clean up worktree
    - needs_input: clean up worktree
    """
    status = task.status

    # Stop chat/planning subprocess if active
    if status in (TaskStatus.PLANNING, TaskStatus.PLANNED):
        chat_mgr = current_app.config.get("CHAT_MANAGER")
        if chat_mgr:
            chat_mgr.abort_session(task_id)

    # Stop worker subprocess if running
    if status in (TaskStatus.RUNNING, TaskStatus.MERGING):
        worker = _active_workers.pop(task_id, None)
        if worker:
            worker.stop()
        # Clean up worktree and branch
        _cleanup_worktree(task_id, task.branch)

    # Clean up worktree for needs_input (worker already exited but worktree may remain)
    if status == TaskStatus.NEEDS_INPUT and task.branch:
        _cleanup_worktree(task_id, task.branch)


def _cleanup_worktree(task_id: str, branch: str | None) -> None:
    """Remove worktree and branch for a task."""
    try:
        is_git = current_app.config.get("IS_GIT", True)
        if not is_git:
            return  # Non-git mode: nothing to clean
        root = current_app.config["PROJECT_ROOT"]
        cfg = current_app.config["CF_CONFIG"]
        wt = WorktreeManager(root, root / cfg.worktree_dir, is_git=is_git)
        wt.remove(task_id, branch)
    except Exception:
        pass  # Best effort cleanup


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
        if tm.remove(tid) is not None:
            deleted.append(tid)
        else:
            failed.append(tid)

    return _ok({"deleted": deleted, "failed": failed, "count": len(deleted)})


# -- 审批 / 反馈 --------------------------------------------------------------

@api_bp.route("/tasks/<task_id>/approve", methods=["POST"])
def approve_task(task_id: str):
    """批准任务。仅对 planned 状态的任务有效。"""
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    if task.status != TaskStatus.PLANNED:
        return _err(f"任务 {task_id} 当前状态为 {task.status.value}，无法审批")

    planner.approve(task)
    tm.update_status(task_id, TaskStatus.APPROVED)

    updated = tm.get(task_id)
    return _ok(updated.to_dict())


    # -- Chat endpoints (replaced reply/feedback) ----------------------------


@api_bp.route("/tasks/<task_id>/chat", methods=["GET"])
def get_chat(task_id: str):
    """Get chat session history for a task.

    Returns thinking=true when AI is generating a response,
    allowing the frontend to poll for completion.
    """
    chat_mgr = current_app.config["CHAT_MANAGER"]
    session = chat_mgr.get_session(task_id)

    if not session:
        return _ok({
            "task_id": task_id,
            "exists": False,
            "thinking": False,
            "messages": [],
        })

    return _ok({
        "task_id": task_id,
        "exists": True,
        "mode": session.mode,
        "status": session.status,
        "thinking": session.thinking,
        "messages": [m.to_dict() for m in session.messages],
    })


@api_bp.route("/tasks/<task_id>/chat", methods=["POST"])
def send_chat(task_id: str):
    """Send a message in the chat session (non-blocking).

    body: {message}. Creates a session if one doesn't exist.
    Returns immediately with accepted=true. The AI response is generated
    in a background thread. Poll GET /tasks/<task_id>/chat to check
    thinking status and retrieve the response when ready.
    """
    tm = current_app.config["TASK_MANAGER"]
    chat_mgr = current_app.config["CHAT_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"Task {task_id} not found", 404)

    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return _err("message is required")

    # Create session if it doesn't exist
    session = chat_mgr.get_session(task_id)
    if not session:
        # Inject auto plan output as initial context if plan file exists
        if task.plan_file:
            plan_path = Path(task.plan_file)
            if plan_path.exists():
                plan_content = plan_path.read_text()
                session = chat_mgr.create_session_from_plan(task_id, plan_content)
            else:
                session = chat_mgr.create_session(task_id, mode="interactive")
        else:
            session = chat_mgr.create_session(task_id, mode="interactive")
        _update_plan_mode(tm, task_id, "interactive")

    if session.status != "active":
        return _err("Chat session is finalized, cannot send new messages")

    if session.thinking:
        return _err("AI is still processing the previous message, please wait")

    # Ensure task is in planning state
    if task.status in (TaskStatus.PENDING, TaskStatus.PLANNED):
        tm.update_status(task_id, TaskStatus.PLANNING)

    # Send message asynchronously (returns immediately)
    accepted = chat_mgr.send_message_async(
        task_id, message, task_prompt=task.prompt
    )

    if not accepted:
        return _err("Failed to start AI response generation")

    return _ok({
        "task_id": task_id,
        "accepted": True,
        "thinking": True,
    })


@api_bp.route("/tasks/<task_id>/chat/finalize", methods=["POST"])
def finalize_chat(task_id: str):
    """Generate a plan document from the chat session (async).

    Finalizes the chat session and triggers plan generation from
    the conversation history.
    """
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    chat_mgr = current_app.config["CHAT_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"Task {task_id} not found", 404)

    session = chat_mgr.get_session(task_id)
    if not session:
        return _err(f"No chat session for task {task_id}", 404)

    if session.status == "finalized":
        return _err("Chat session is already finalized, plan generation in progress")

    if not session.messages:
        return _err("Chat session has no messages")

    # Mark session as finalized
    chat_mgr.finalize(task_id)

    # Transition to planning
    tm.update_status(task_id, TaskStatus.PLANNING)

    # Generate plan in background thread
    def _generate():
        try:
            plan_file = planner.generate_from_chat(task, session)
            if plan_file:
                tm.update_status(task_id, TaskStatus.PLANNED)
                _update_plan_file(tm, task_id, str(plan_file))
            else:
                tm.update_status(
                    task_id, TaskStatus.FAILED,
                    task.error or "Plan generation from chat failed",
                )
        except Exception as e:
            tm.update_status(task_id, TaskStatus.FAILED, str(e))

    thread = threading.Thread(target=_generate, daemon=True)
    thread.start()

    updated = tm.get(task_id)
    return _ok(updated.to_dict())


# -- 补充输入 ----------------------------------------------------------------

@api_bp.route("/tasks/<task_id>/respond", methods=["POST"])
def respond_task(task_id: str):
    """为 needs_input 状态的任务补充信息。body: {message}"""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    if task.status != TaskStatus.NEEDS_INPUT:
        return _err(f"任务 {task_id} 当前状态为 {task.status.value}，不需要补充输入")

    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return _err("message 不能为空")

    updated = tm.respond(task_id, message)
    if not updated:
        return _err(f"补充输入失败")

    return _ok(updated.to_dict())


# -- 全局状态 / Worker 状态 ----------------------------------------------------

@api_bp.route("/status", methods=["GET"])
def global_status():
    """获取全局状态概览。"""
    tm = current_app.config["TASK_MANAGER"]
    tasks = tm.list_tasks()

    counts = {}
    for status in TaskStatus:
        counts[status.value] = 0
    for t in tasks:
        counts[t.status.value] += 1

    return _ok({
        "total": len(tasks),
        "counts": counts,
    })


@api_bp.route("/overview", methods=["GET"])
def overview():
    """Overview dashboard data: counts, workers, recent activity, timeline."""
    from datetime import datetime, timedelta

    tm = current_app.config["TASK_MANAGER"]
    tasks = tm.list_tasks()

    # -- Status counts
    counts = {}
    for status in TaskStatus:
        counts[status.value] = 0
    for t in tasks:
        counts[t.status.value] += 1

    # -- Active workers
    project_root = current_app.config["PROJECT_ROOT"]
    monitor_file = project_root / ".claude-flow" / "monitor.json"
    workers = []
    if monitor_file.exists():
        try:
            data = json.loads(monitor_file.read_text())
            workers = data.get("workers", [])
        except (json.JSONDecodeError, OSError):
            pass
    if not workers:
        running = [t for t in tasks if t.status == TaskStatus.RUNNING]
        for t in running:
            if t.worker_id is not None:
                workers.append({
                    "worker_id": t.worker_id,
                    "task_id": t.id,
                    "task_title": t.title,
                    "started_at": t.started_at.isoformat() if t.started_at else None,
                })

    # -- Recent activity (last 10 finished / failed tasks, sorted by time desc)
    recent = sorted(
        [t for t in tasks if t.status in (TaskStatus.DONE, TaskStatus.FAILED)
         and t.completed_at is not None],
        key=lambda t: t.completed_at,
        reverse=True,
    )[:10]
    recent_list = []
    for t in recent:
        recent_list.append({
            "id": t.id,
            "title": t.title,
            "status": t.status.value,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        })

    # -- Pipeline: tasks currently in each active phase
    pipeline = {
        "pending": [{"id": t.id, "title": t.title, "priority": t.priority}
                    for t in tasks if t.status == TaskStatus.PENDING],
        "planning": [{"id": t.id, "title": t.title}
                     for t in tasks if t.status in (TaskStatus.PLANNING, TaskStatus.PLANNED)],
        "running": [{"id": t.id, "title": t.title, "worker_id": t.worker_id,
                     "started_at": t.started_at.isoformat() if t.started_at else None}
                    for t in tasks if t.status == TaskStatus.RUNNING],
        "needs_input": [{"id": t.id, "title": t.title}
                        for t in tasks if t.status == TaskStatus.NEEDS_INPUT],
    }

    # -- Completion rate
    done_count = counts.get("done", 0)
    failed_count = counts.get("failed", 0)
    finished = done_count + failed_count
    success_rate = round(done_count / finished * 100, 1) if finished > 0 else None

    return _ok({
        "total": len(tasks),
        "counts": counts,
        "workers": workers,
        "recent_activity": recent_list,
        "pipeline": pipeline,
        "success_rate": success_rate,
        "done_count": done_count,
        "failed_count": failed_count,
    })


@api_bp.route("/workers", methods=["GET"])
def worker_status():
    """获取 worker 状态（读取 monitor 状态文件）。"""
    project_root = current_app.config["PROJECT_ROOT"]
    monitor_file = project_root / ".claude-flow" / "monitor.json"

    if monitor_file.exists():
        try:
            data = json.loads(monitor_file.read_text())
            return _ok(data)
        except (json.JSONDecodeError, OSError):
            return _ok({"workers": [], "note": "状态文件读取失败"})
    else:
        # 从任务列表中推断活跃 worker
        tm = current_app.config["TASK_MANAGER"]
        running = tm.list_tasks(status=TaskStatus.RUNNING)
        workers = []
        for t in running:
            if t.worker_id is not None:
                workers.append({
                    "worker_id": t.worker_id,
                    "task_id": t.id,
                    "task_title": t.title,
                    "started_at": t.started_at.isoformat() if t.started_at else None,
                })
        return _ok({"workers": workers})


# -- Plan 生成 ----------------------------------------------------------------

@api_bp.route("/tasks/<task_id>/plan", methods=["POST"])
def plan_task(task_id: str):
    """Trigger plan generation (async).

    body (optional): {mode: "auto"|"interactive"}
    - auto (default): AI generates plan directly in background.
    - interactive: Creates a chat session for multi-round planning.
    """
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    chat_mgr = current_app.config["CHAT_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"Task {task_id} not found", 404)

    if task.is_mini:
        return _err(f"Task {task_id} is a mini task (no planning needed)")

    if task.status != TaskStatus.PENDING:
        return _err(
            f"Task {task_id} is {task.status.value}, only pending tasks can start planning"
        )

    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "auto")

    if mode not in ("auto", "interactive"):
        return _err(f"Invalid mode: {mode}, must be 'auto' or 'interactive'")

    # Update plan_mode on task
    _update_plan_mode(tm, task_id, mode)

    if mode == "interactive":
        # Create chat session and set status to planning
        chat_mgr.create_session(task_id, mode="interactive")
        tm.update_status(task_id, TaskStatus.PLANNING)
        updated = tm.get(task_id)
        return _ok(updated.to_dict())

    # Auto mode: generate plan in background
    tm.update_status(task_id, TaskStatus.PLANNING)

    def _generate():
        try:
            plan_file = planner.generate(task)
            if plan_file:
                tm.update_status(task_id, TaskStatus.PLANNED)
                _update_plan_file(tm, task_id, str(plan_file))
            else:
                tm.update_status(
                    task_id, TaskStatus.FAILED,
                    task.error or "Plan generation failed",
                )
        except Exception as e:
            tm.update_status(task_id, TaskStatus.FAILED, str(e))

    thread = threading.Thread(target=_generate, daemon=True)
    thread.start()

    updated = tm.get(task_id)
    return _ok(updated.to_dict())


@api_bp.route("/plan-all", methods=["POST"])
def plan_all_tasks():
    """为所有 pending 状态的任务批量触发计划生成。"""
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    pending = [t for t in tm.list_tasks(status=TaskStatus.PENDING) if not t.is_mini]

    if not pending:
        return _ok({"planned": 0, "message": "没有 pending 状态的任务"})

    count = 0
    for task in pending:
        tm.update_status(task.id, TaskStatus.PLANNING)

        def _generate(t=task):
            try:
                plan_file = planner.generate(t)
                if plan_file:
                    tm.update_status(t.id, TaskStatus.PLANNED)
                    _update_plan_file(tm, t.id, str(plan_file))
                else:
                    tm.update_status(t.id, TaskStatus.FAILED, t.error or "Plan generation failed")
            except Exception as e:
                tm.update_status(t.id, TaskStatus.FAILED, str(e))

        thread = threading.Thread(target=_generate, daemon=True)
        thread.start()
        count += 1

    return _ok({"planned": count, "message": f"已为 {count} 个任务启动计划生成"})


@api_bp.route("/tasks/<task_id>/plan", methods=["GET"])
def get_plan(task_id: str):
    """获取任务的计划内容。"""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    project_root = current_app.config["PROJECT_ROOT"]
    plans_dir = project_root / ".claude-flow" / "plans"

    # 优先使用 task 上记录的 plan_file
    plan_path = None
    if task.plan_file:
        plan_path = Path(task.plan_file)
    if not plan_path or not plan_path.exists():
        plan_path = plans_dir / f"{task_id}.md"

    if not plan_path.exists():
        # Return a non-error response when plan is being generated
        if task.status == TaskStatus.PLANNING:
            return _ok({"task_id": task_id, "content": None, "generating": True})
        return _err(f"任务 {task_id} 的计划文件不存在", 404)

    content = plan_path.read_text()
    return _ok({"task_id": task_id, "content": content})


# -- 批量审批 ------------------------------------------------------------------

@api_bp.route("/approve-all", methods=["POST"])
def approve_all_tasks():
    """批准所有 planned 状态的任务。"""
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    planned = tm.list_tasks(status=TaskStatus.PLANNED)

    count = 0
    for task in planned:
        planner.approve(task)
        tm.update_status(task.id, TaskStatus.APPROVED)
        count += 1

    return _ok({"approved": count})


# -- 任务执行 ------------------------------------------------------------------

@api_bp.route("/tasks/<task_id>/run", methods=["POST"])
def run_task(task_id: str):
    """触发单个任务执行（异步）。将任务设为 approved 后在后台 worker 执行。"""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    if task.status not in (TaskStatus.APPROVED, TaskStatus.PLANNED):
        return _err(f"任务 {task_id} 当前状态为 {task.status.value}，需要 approved 或 planned 状态")

    # 如果是 planned 状态，先批准
    if task.status == TaskStatus.PLANNED:
        planner = current_app.config["PLANNER"]
        planner.approve(task)
        tm.update_status(task_id, TaskStatus.APPROVED)

    # 在后台线程中执行 — 提前在请求上下文中读取所有配置，
    # 避免后台线程中访问 current_app 导致 "Working outside of application context" 错误
    project_root = current_app.config["PROJECT_ROOT"]
    cfg = current_app.config["CF_CONFIG"]
    is_git = current_app.config.get("IS_GIT", True)

    def _execute():
        from ..worker import Worker
        from ..worktree import WorktreeManager
        from ..task_manager import TaskManager as TM

        # 使用独立的 TaskManager 实例避免线程竞争
        local_tm = TM(project_root)
        wt = WorktreeManager(project_root, project_root / cfg.worktree_dir, is_git=is_git)
        worker = Worker(0, project_root, local_tm, wt, cfg, is_git=is_git)
        claimed = local_tm.claim_next(0)
        if claimed:
            _active_workers[claimed.id] = worker
            try:
                worker.execute_task(claimed)
            finally:
                _active_workers.pop(claimed.id, None)

    thread = threading.Thread(target=_execute, daemon=True)
    thread.start()

    updated = tm.get(task_id)
    return _ok(updated.to_dict())


@api_bp.route("/run", methods=["POST"])
def run_all_tasks():
    """启动 worker 执行所有 approved 任务（异步）。body: {num_workers, daemon}"""
    tm = current_app.config["TASK_MANAGER"]
    project_root = current_app.config["PROJECT_ROOT"]
    cfg = current_app.config["CF_CONFIG"]

    data = request.get_json(silent=True) or {}
    num_workers = int(data.get("num_workers", 1))
    daemon = bool(data.get("daemon", False))

    approved = tm.list_tasks(status=TaskStatus.APPROVED)
    if not approved and not daemon:
        return _ok({"started": 0, "message": "没有 approved 状态的任务"})

    is_git = current_app.config.get("IS_GIT", True)

    def _run_workers():
        import logging
        logging.basicConfig(level=logging.INFO, format="%(message)s")

        from ..worker import Worker
        from ..worktree import WorktreeManager
        from ..task_manager import TaskManager as TM
        for wid in range(num_workers):
            local_tm = TM(project_root)
            wt = WorktreeManager(project_root, project_root / cfg.worktree_dir, is_git=is_git)
            w = Worker(wid, project_root, local_tm, wt, cfg, is_git=is_git)
            if daemon:
                thread = threading.Thread(
                    target=w.run_daemon, args=(_active_workers,), daemon=True
                )
            else:
                thread = threading.Thread(
                    target=w.run_loop, args=(_active_workers,), daemon=True
                )
            thread.start()

    thread = threading.Thread(target=_run_workers, daemon=True)
    thread.start()

    return _ok({
        "started": num_workers,
        "daemon": daemon,
        "pending_tasks": len(approved),
        "message": f"已启动 {num_workers} 个 worker",
    })


# -- 任务重置 ------------------------------------------------------------------

@api_bp.route("/tasks/<task_id>/reset", methods=["POST"])
def reset_task(task_id: str):
    """重置任务状态为 pending。适用于 failed 和 needs_input 状态。"""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    if task.status == TaskStatus.RUNNING:
        # Reset zombie running task (worker crashed without updating status)
        target = TaskStatus.PLANNED if task.plan_file else TaskStatus.PENDING
        tm.update_status(task_id, target)
        # Clean up orphaned worktree and branch (git repos only)
        is_git = current_app.config.get("IS_GIT", True)
        if is_git:
            root = current_app.config["PROJECT_ROOT"]
            cfg = current_app.config["CF_CONFIG"]
            wt = WorktreeManager(root, root / cfg.worktree_dir, is_git=is_git)
            wt.remove(task_id, task.branch)
        updated = tm.get(task_id)
        return _ok(updated.to_dict())

    if task.status not in (TaskStatus.FAILED, TaskStatus.NEEDS_INPUT):
        return _err(f"任务 {task_id} 当前状态为 {task.status.value}，仅 failed/needs_input/running 可重置")

    # Mini tasks reset to APPROVED (skip planning), normal tasks to PENDING
    reset_target = TaskStatus.APPROVED if task.is_mini else TaskStatus.PENDING
    tm.update_status(task_id, reset_target)
    updated = tm.get(task_id)
    return _ok(updated.to_dict())


# -- 任务日志 ------------------------------------------------------------------

@api_bp.route("/tasks/<task_id>/log", methods=["GET"])
def get_task_log(task_id: str):
    """获取任务执行日志。

    优先返回结构化 JSON 日志（structured=true），回退到原始文本。
    """
    import json as _json

    project_root = current_app.config["PROJECT_ROOT"]
    logs_dir = project_root / ".claude-flow" / "logs"

    # Prefer structured JSON log
    json_file = logs_dir / f"{task_id}.json"
    if json_file.exists():
        try:
            log_data = _json.loads(json_file.read_text())
            return _ok({
                "task_id": task_id,
                "exists": True,
                "structured": True,
                "data": log_data,
            })
        except (ValueError, OSError):
            pass  # Fall through to raw log

    # Fallback to raw log
    raw_file = logs_dir / f"{task_id}.log"
    if not raw_file.exists():
        return _ok({"task_id": task_id, "content": "", "exists": False})

    content = raw_file.read_text()
    return _ok({"task_id": task_id, "content": content, "exists": True, "structured": False})


# -- 重试所有失败任务 -----------------------------------------------------------

@api_bp.route("/retry-all", methods=["POST"])
def retry_all_tasks():
    """将所有 failed 任务重置为 approved 以便重试。"""
    tm = current_app.config["TASK_MANAGER"]
    failed = tm.list_tasks(status=TaskStatus.FAILED)

    count = 0
    for task in failed:
        tm.update_status(task.id, TaskStatus.APPROVED)
        count += 1

    return _ok({"retried": count})


# -- Token Usage ---------------------------------------------------------------

@api_bp.route("/usage/summary", methods=["GET"])
def usage_summary():
    """Get aggregated usage summary (total tokens, cost, sessions)."""
    um = current_app.config["USAGE_MANAGER"]
    since = request.args.get("since")
    until = request.args.get("until")
    data = um.get_summary(since=since, until=until)
    return _ok(data)


@api_bp.route("/usage/sessions", methods=["GET"])
def usage_sessions():
    """Get per-session (task) usage report."""
    um = current_app.config["USAGE_MANAGER"]
    since = request.args.get("since")
    until = request.args.get("until")
    data = um.get_session_usage(since=since, until=until)
    return _ok(data)


@api_bp.route("/usage/daily", methods=["GET"])
def usage_daily():
    """Get daily aggregated usage report."""
    um = current_app.config["USAGE_MANAGER"]
    since = request.args.get("since")
    until = request.args.get("until")
    data = um.get_daily_usage(since=since, until=until)
    if data is None:
        return _ok([])
    return _ok(data)


@api_bp.route("/usage/monthly", methods=["GET"])
def usage_monthly():
    """Get monthly aggregated usage report."""
    um = current_app.config["USAGE_MANAGER"]
    since = request.args.get("since")
    until = request.args.get("until")
    data = um.get_monthly_usage(since=since, until=until)
    if data is None:
        return _ok([])
    return _ok(data)


# -- 辅助函数 ------------------------------------------------------------------

def _update_plan_file(tm, task_id: str, plan_file_path: str):
    """Update task's plan_file field (thread-safe via file lock)."""
    def _do():
        tasks = tm._load()
        for t in tasks:
            if t.id == task_id:
                t.plan_file = plan_file_path
                tm._save(tasks)
                return
    tm._with_lock(_do)


def _update_plan_mode(tm, task_id: str, mode: str):
    """Update task's plan_mode field (thread-safe via file lock)."""
    def _do():
        tasks = tm._load()
        for t in tasks:
            if t.id == task_id:
                t.plan_mode = mode
                tm._save(tasks)
                return
    tm._with_lock(_do)


# -- Submodules endpoint ----------------------------------------------------

@api_bp.route("/submodules", methods=["GET"])
def list_submodules():
    """返回项目中所有 submodule 的路径列表（从 .gitmodules 解析）。"""
    import configparser

    is_git = current_app.config.get("IS_GIT", True)
    if not is_git:
        return _ok([])

    root = current_app.config["PROJECT_ROOT"]
    gitmodules_path = root / ".gitmodules"
    if not gitmodules_path.exists():
        return _ok([])

    parser = configparser.ConfigParser()
    parser.read(str(gitmodules_path))

    paths = []
    for section in parser.sections():
        if parser.has_option(section, "path"):
            paths.append(parser.get(section, "path"))

    return _ok(paths)


# -- Mini Task endpoints ----------------------------------------------------

@api_bp.route("/mini-tasks", methods=["GET"])
def list_mini_tasks():
    """List all mini tasks."""
    tm = current_app.config["TASK_MANAGER"]
    tasks = tm.list_tasks(task_type="mini")
    return _ok([t.to_dict() for t in tasks])


@api_bp.route("/mini-tasks", methods=["POST"])
def create_mini_task():
    """Create a mini task. body: {title, prompt}"""
    tm = current_app.config["TASK_MANAGER"]
    data = request.get_json(silent=True)

    if not data:
        return _err("request body cannot be empty")

    title = data.get("title")
    prompt = data.get("prompt", "")

    if not title:
        return _err("title is required")

    submodules = data.get("submodules", [])
    if not isinstance(submodules, list):
        return _err("submodules must be an array")

    task = tm.add_mini(title, prompt, submodules=submodules)
    return _ok(task.to_dict()), 201


@api_bp.route("/mini-tasks/<task_id>/start", methods=["POST"])
def start_mini_task(task_id: str):
    """Start a mini task: create worktree, start PTY session, mark running."""
    import subprocess
    from datetime import datetime

    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"Task {task_id} not found", 404)
    if task.task_type.value != "mini":
        return _err(f"Task {task_id} is not a mini task")
    if task.status not in (TaskStatus.PENDING, TaskStatus.INTERRUPTED, TaskStatus.APPROVED):
        return _err(f"Task {task_id} is {task.status.value}, cannot start")

    root = current_app.config["PROJECT_ROOT"]
    cfg = current_app.config["CF_CONFIG"]
    pty_mgr = current_app.config.get("PTY_MANAGER")

    if not pty_mgr:
        return _err("PTY manager not available", 500)

    # Create worktree
    branch = f"cf/{task_id}"
    is_git = current_app.config.get("IS_GIT", True)
    wt = WorktreeManager(root, root / cfg.worktree_dir, is_git=is_git)
    try:
        wt_path = wt.create(task_id, branch, config=cfg,
                            submodules=task.submodules or None)
    except subprocess.CalledProcessError as e:
        return _err(f"Worktree creation failed: {e.stderr}", 500)

    # Update task status and branch
    tm.update_status(task_id, TaskStatus.RUNNING)
    def _set_branch():
        tasks = tm._load()
        for t in tasks:
            if t.id == task_id:
                t.branch = branch
                t.started_at = datetime.now()
                tm._save(tasks)
                return
    tm._with_lock(_set_branch)

    # Start PTY session
    try:
        pty_mgr.create_session(
            task_id, wt_path,
            prompt=task.prompt,
            skip_permissions=cfg.skip_permissions,
        )
    except Exception as e:
        tm.update_status(task_id, TaskStatus.FAILED, f"PTY creation failed: {e}")
        wt.remove(task_id, branch)
        return _err(f"PTY creation failed: {e}", 500)

    updated = tm.get(task_id)
    return _ok({**updated.to_dict(), "ws_url": f"/ws/terminal/{task_id}"})


@api_bp.route("/mini-tasks/<task_id>/stop", methods=["POST"])
def stop_mini_task(task_id: str):
    """Stop PTY session, auto-commit, transition to merging."""
    import subprocess as sp

    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"Task {task_id} not found", 404)
    if task.status != TaskStatus.RUNNING:
        return _err(f"Task {task_id} is {task.status.value}, cannot stop")

    pty_mgr = current_app.config.get("PTY_MANAGER")
    if pty_mgr:
        pty_mgr.remove_session(task_id)

    # Auto-commit changes in worktree
    root = current_app.config["PROJECT_ROOT"]
    cfg = current_app.config["CF_CONFIG"]
    wt_path = root / cfg.worktree_dir / task_id

    if wt_path.exists():
        sp.run(["git", "add", "-A"], cwd=str(wt_path), capture_output=True)
        sp.run(
            ["git", "commit", "-m", f"feat({task_id}): {task.title}", "--no-verify"],
            cwd=str(wt_path), capture_output=True,
        )

    tm.update_status(task_id, TaskStatus.MERGING)
    updated = tm.get(task_id)
    return _ok(updated.to_dict())


@api_bp.route("/mini-tasks/<task_id>/diff", methods=["GET"])
def mini_task_diff(task_id: str):
    """Get git diff for merge preview."""
    import subprocess as sp

    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"Task {task_id} not found", 404)
    if not task.branch:
        return _err(f"Task {task_id} has no branch")

    root = current_app.config["PROJECT_ROOT"]
    cfg = current_app.config["CF_CONFIG"]

    result = sp.run(
        ["git", "diff", f"{cfg.main_branch}...{task.branch}"],
        cwd=str(root), capture_output=True, text=True,
    )
    stat_result = sp.run(
        ["git", "diff", "--stat", f"{cfg.main_branch}...{task.branch}"],
        cwd=str(root), capture_output=True, text=True,
    )

    return _ok({
        "task_id": task_id,
        "diff": result.stdout,
        "stat": stat_result.stdout,
        "branch": task.branch,
    })


@api_bp.route("/mini-tasks/<task_id>/merge", methods=["POST"])
def merge_mini_task(task_id: str):
    """Merge mini task branch, clean up worktree."""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"Task {task_id} not found", 404)
    if task.status != TaskStatus.MERGING:
        return _err(f"Task {task_id} is {task.status.value}, must be merging")

    root = current_app.config["PROJECT_ROOT"]
    cfg = current_app.config["CF_CONFIG"]
    is_git = current_app.config.get("IS_GIT", True)
    wt = WorktreeManager(root, root / cfg.worktree_dir, is_git=is_git)

    success = wt.merge(
        task.branch, cfg.main_branch, cfg.merge_strategy,
        config=cfg, task_title=task.title, task_prompt=task.prompt,
    )

    if not success:
        tm.update_status(task_id, TaskStatus.FAILED, "Merge conflict")
        return _err("Merge failed due to conflicts")

    wt.remove(task_id, task.branch)
    if cfg.auto_push:
        wt.push(cfg.main_branch)

    tm.update_status(task_id, TaskStatus.DONE)
    updated = tm.get(task_id)
    return _ok(updated.to_dict())


@api_bp.route("/mini-tasks/<task_id>/discard", methods=["POST"])
def discard_mini_task(task_id: str):
    """Discard mini task -- remove worktree without merging."""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"Task {task_id} not found", 404)

    pty_mgr = current_app.config.get("PTY_MANAGER")
    if pty_mgr:
        pty_mgr.remove_session(task_id)

    if task.branch:
        root = current_app.config["PROJECT_ROOT"]
        cfg = current_app.config["CF_CONFIG"]
        is_git = current_app.config.get("IS_GIT", True)
        wt = WorktreeManager(root, root / cfg.worktree_dir, is_git=is_git)
        wt.remove(task_id, task.branch)

    tm.update_status(task_id, TaskStatus.FAILED, "Discarded by user")
    updated = tm.get(task_id)
    return _ok(updated.to_dict())
