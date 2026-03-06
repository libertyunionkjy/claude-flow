"""REST API 蓝图，提供任务管理和状态查询接口。"""
from __future__ import annotations

import json

try:
    from flask import Blueprint, current_app, jsonify, request
except ImportError:
    raise ImportError(
        "Flask 未安装。请运行 `pip install flask` 以启用 Web Manager 功能。"
    )

from ..models import TaskStatus

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _ok(data):
    """成功响应。"""
    return jsonify({"ok": True, "data": data})


def _err(message: str, status_code: int = 400):
    """错误响应。"""
    return jsonify({"ok": False, "error": message}), status_code


# -- 任务列表 / 创建 ----------------------------------------------------------

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

    return _ok([t.to_dict() for t in tasks])


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

    task = tm.add(title, prompt, priority=priority)
    return _ok(task.to_dict()), 201


# -- 单个任务操作 --------------------------------------------------------------

@api_bp.route("/tasks/<task_id>", methods=["GET"])
def get_task(task_id: str):
    """获取单个任务详情。"""
    tm = current_app.config["TASK_MANAGER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    return _ok(task.to_dict())


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
    """删除任务。"""
    tm = current_app.config["TASK_MANAGER"]

    if tm.remove(task_id):
        return _ok({"deleted": task_id})
    else:
        return _err(f"任务 {task_id} 不存在", 404)


# -- 审批 / 拒绝 --------------------------------------------------------------

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


@api_bp.route("/tasks/<task_id>/reject", methods=["POST"])
def reject_task(task_id: str):
    """拒绝任务。body: {reason}。仅对 planned 状态的任务有效。"""
    tm = current_app.config["TASK_MANAGER"]
    planner = current_app.config["PLANNER"]
    task = tm.get(task_id)

    if not task:
        return _err(f"任务 {task_id} 不存在", 404)

    if task.status != TaskStatus.PLANNED:
        return _err(f"任务 {task_id} 当前状态为 {task.status.value}，无法拒绝")

    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "未提供原因")

    planner.reject(task, reason)
    tm.update_status(task_id, TaskStatus.PENDING)

    updated = tm.get(task_id)
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
