"""Flask 应用工厂，创建并配置 Web Manager 看板应用。"""
from __future__ import annotations

from pathlib import Path

try:
    from flask import Flask, render_template
except ImportError:
    raise ImportError(
        "Flask 未安装。请运行 `pip install flask` 以启用 Web Manager 功能。"
    )

from ..chat import ChatManager
from ..config import Config
from ..models import TaskStatus
from ..planner import Planner
from ..task_manager import TaskManager
from ..usage import UsageManager


def _recover_stuck_planning_tasks(
    task_manager: TaskManager,
    chat_manager: ChatManager,
    plans_dir: Path,
) -> None:
    """Recover tasks stuck in PLANNING state due to interrupted finalize.

    When the server restarts, daemon threads running generate_from_chat
    are killed without updating task status.  This finds tasks in PLANNING
    state whose chat session is already finalized but have no plan file,
    and resets the chat session to 'active' so the user can re-trigger
    finalize.
    """
    tasks = task_manager.list_tasks()
    for task in tasks:
        if task.status != TaskStatus.PLANNING:
            continue
        session = chat_manager.get_session(task.id)
        if not session:
            continue
        plan_file = plans_dir / f"{task.id}.md"
        if session.status == "finalized" and not plan_file.exists():
            # Chat was finalized but plan generation was interrupted
            session.status = "active"
            session.thinking = False
            chat_manager._save_session(session)


def create_app(project_root: Path, config: Config) -> Flask:
    """创建并配置 Flask 应用。

    在内部实例化 TaskManager 和 Planner，并存入 app.config 供 API 蓝图使用。

    Args:
        project_root: 目标项目根目录路径。
        config: Claude Flow 配置对象。

    Returns:
        配置完成的 Flask 应用实例。
    """
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )

    # 实例化核心管理器并存入 app.config
    task_manager = TaskManager(project_root)
    plans_dir = project_root / ".claude-flow" / "plans"
    planner = Planner(project_root, plans_dir, config)

    chat_manager = ChatManager(project_root, config)

    app.config["PROJECT_ROOT"] = project_root
    app.config["CF_CONFIG"] = config
    app.config["TASK_MANAGER"] = task_manager
    app.config["PLANNER"] = planner
    app.config["CHAT_MANAGER"] = chat_manager
    app.config["USAGE_MANAGER"] = UsageManager(project_root, config)

    # Recover tasks stuck in PLANNING state due to interrupted finalize
    _recover_stuck_planning_tasks(task_manager, chat_manager, plans_dir)

    # 注册 API 蓝图
    from .api import api_bp
    app.register_blueprint(api_bp)

    # 看板首页路由
    @app.route("/")
    def index():
        return render_template("index.html")

    return app
