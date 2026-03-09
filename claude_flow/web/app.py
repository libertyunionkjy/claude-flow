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
from ..planner import Planner
from ..task_manager import TaskManager
from ..usage import UsageManager


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

    # 注册 API 蓝图
    from .api import api_bp
    app.register_blueprint(api_bp)

    # 看板首页路由
    @app.route("/")
    def index():
        return render_template("index.html")

    return app
